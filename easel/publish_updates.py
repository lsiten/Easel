"""Fenced, reviewed changes to an existing published task's provider work."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hmac
from pathlib import Path
import time
import uuid

from easel._channel_publish_media import credential_revision, frozen_paths
from easel._channel_publish_worker import lease_heartbeat
from easel._publish_update_preparation import editable_work, normalize_update, prepare_update
from easel.channels import ChannelAdapter, ChannelError, Credential, CredentialStore, PublishContent, PublishReceipt, definition
from easel.ownership import freeze_media, validate_persona
from easel.publish_ledger import PublishLedger
from easel.publish_models import IdempotencyConflict, JsonValue, PublishLease, PublishRun, RunState, digest_mapping
from easel.publish_policy import publishing_allowed
from easel.publish_recovery import worker_owner
from easel.publish_tasks import TaskStore
from easel.publish_update_models import PublicationUpdate, UpdateApproval, UpdateHistory, UpdateIntent, UpdateRequest, UpdateView


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_key(task_id: str, request_key: str) -> str:
    return digest_mapping({'task_id': task_id, 'request_key': request_key})


class PublishUpdates:
    def __init__(self, root: Path, settings: Callable[[], Mapping[str, str]],
                 adapter_factory: Callable[[str], ChannelAdapter] = ChannelAdapter) -> None:
        self.root, self.settings = root, settings
        self.ledger = PublishLedger(root / '_publish/ledger.sqlite3')
        self.tasks = TaskStore(root / '_publish')
        self.store = CredentialStore(root / '_channels')
        self.adapter_factory = (lambda platform: ChannelAdapter(platform, settings=self.settings(), store=self.store)) if adapter_factory is ChannelAdapter else adapter_factory

    def precheck(self, task_id: str, intent: UpdateIntent) -> UpdateApproval:
        run = self._target(task_id, intent)
        adapter = self.adapter_factory(run.platform)
        credential = self.store.get(run.persona, definition(run.platform).platform, run.account_id)
        return prepare_update(self.root, adapter, credential, run, task_id, intent).approval

    def update(self, task_id: str, request: UpdateRequest) -> UpdateView:
        """Advance metadata and thumbnail checkpoints without invoking initial publication."""
        run = self._target(task_id, request)
        self._allowed(run.platform)
        if request.reviewed is not True:
            raise ChannelError(run.platform, 'explicit_review_required', status=403)
        intent = normalize_update(run.platform, UpdateIntent.model_validate(request.model_dump(exclude={
            'reviewed', 'request_key', 'viewed_revision', 'approval_digest', 'approval_expires_at'})))
        lease = self.ledger.acquire_lease(run.run_id, worker_owner())
        try:
            with lease_heartbeat(self.ledger, lease):
                current = self._target(task_id, request)
                history = UpdateHistory.model_validate(current.payload.get('publication_updates') or {})
                record = history.records.get(_record_key(task_id, request.request_key))
                adapter = self.adapter_factory(run.platform)
                credential = self.store.get(run.persona, definition(run.platform).platform, run.account_id)
                if record is not None:
                    if record.intent != intent or record.viewed_revision != request.viewed_revision or not hmac.compare_digest(record.approval_digest.encode(), request.approval_digest.encode()):
                        raise IdempotencyConflict('更新 key 已绑定其他审核内容')
                    if record.state in {'succeeded', 'unknown'}:
                        return self._view(record)
                    if record.inflight and record.stage == 'metadata':
                        record = record.model_copy(update={'state': 'unknown', 'inflight': False, 'error': 'update_outcome_unknown'})
                        self._save(current, record, lease)
                        return self._view(record)
                    if credential_revision(credential) != record.credential_revision:
                        raise ChannelError(run.platform, 'credential_changed', status=409)
                else:
                    prepared = prepare_update(self.root, adapter, credential, current, task_id, intent, request.approval_expires_at)
                    if request.viewed_revision != prepared.approval.viewed_revision or not hmac.compare_digest(request.approval_digest.encode(), prepared.approval.approval_digest.encode()):
                        raise ChannelError(run.platform, 'update_review_changed', status=409)
                    record = PublicationUpdate(update_id=uuid.uuid4().hex, task_id=task_id, run_id=run.run_id,
                        platform=run.platform, platform_work_id=run.platform_work_id, intent=intent,
                        request_key=request.request_key, approval_digest=request.approval_digest, viewed_revision=request.viewed_revision,
                        credential_revision=prepared.credential_revision, cover_manifest=prepared.cover_manifest,
                        cover_status='pending' if prepared.cover_manifest else '', created_at=_stamp(), updated_at=_stamp())
                    current = self._save(current, record, lease)
                if record.state == 'prepared' and record.cover_manifest:
                    try:
                        frozen_paths(self.root, run.persona, record.update_id, record.cover_manifest)
                    except ChannelError as error:
                        if error.code != 'frozen_media_missing':
                            raise
                        freeze_media(self.root, run.persona, record.update_id, record.cover_manifest)
                if record.stage == 'metadata':
                    self._allowed(run.platform)
                    credential = self._credential(record)
                    record = record.model_copy(update={'state': 'pending', 'inflight': True})
                    current = self._save(current, record, lease)
                    try:
                        latest = adapter.work(credential, run.platform_work_id)
                        expected_etag = str(latest.get('etag') or '') if run.platform == 'youtube' else ''
                        if digest_mapping(editable_work(run.platform, latest)) != record.viewed_revision:
                            raise ChannelError(run.platform, 'work_revision_changed', status=409)
                        adapter.update(credential, run.platform_work_id,
                            PublishContent(title=intent.title, body=intent.body, options=intent.options), expected_etag=expected_etag)
                    except ChannelError as error:
                        record = record.model_copy(update={'state': 'unknown', 'inflight': False, 'error': error.code})
                        self._save(current, record, lease)
                        return self._view(record)
                    record = record.model_copy(update={'metadata_applied': True, 'inflight': False,
                        'stage': 'cover' if record.cover_manifest else 'complete',
                        'state': 'pending' if record.cover_manifest else 'succeeded'})
                    current = self._save(current, record, lease, refresh=True)
                if record.stage == 'cover':
                    self._allowed(run.platform)
                    credential = self._credential(record)
                    paths = frozen_paths(self.root, run.persona, record.update_id, record.cover_manifest)
                    record = record.model_copy(update={'inflight': True})
                    current = self._save(current, record, lease)
                    try:
                        adapter.apply_cover(credential, PublishReceipt(state='published',
                            platform_work_id=run.platform_work_id, permalink=run.permalink), paths[0])
                        record = record.model_copy(update={'cover_status': 'applied', 'error': ''})
                    except ChannelError as error:
                        record = record.model_copy(update={'cover_status': 'failed', 'error': error.code})
                    record = record.model_copy(update={'state': 'succeeded', 'stage': 'complete', 'inflight': False})
                    self._save(current, record, lease)
                return self._view(record)
        finally:
            self.ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)

    def get(self, task_id: str, update_id: str, scope: UpdateIntent) -> UpdateView:
        run = self._target(task_id, scope)
        records = UpdateHistory.model_validate(run.payload.get('publication_updates') or {}).records.values()
        record = next((item for item in records if item.update_id == update_id and item.task_id == task_id), None)
        if record is None:
            raise ChannelError(run.platform, 'update_not_found', status=404)
        return self._view(record)

    def _target(self, task_id: str, scope: UpdateIntent) -> PublishRun:
        validate_persona(scope.persona)
        try:
            task = self.tasks.task(scope.persona, task_id)
        except KeyError as error:
            raise ChannelError('update', 'task_not_found', status=404) from error
        run = self.ledger.get_run(str(task['run_id']))
        if run is None or (run.persona, run.platform, run.account_id) != (scope.persona, task['platform'], scope.account_uid) or task['account_uid'] != scope.account_uid:
            raise ChannelError('update', 'task_not_found', status=404)
        if run.state != RunState.PUBLISHED or not run.platform_work_id:
            raise ChannelError(run.platform, 'published_work_required', status=409)
        if run.platform not in {'youtube', 'pinterest'}:
            raise ChannelError(run.platform, 'update_unsupported', status=405)
        return run

    def _save(self, run: PublishRun, record: PublicationUpdate, lease: PublishLease, *, refresh: bool = False) -> PublishRun:
        history = UpdateHistory.model_validate(run.payload.get('publication_updates') or {})
        updated = record.model_copy(update={'updated_at': _stamp()})
        records = {**history.records, _record_key(record.task_id, record.request_key): updated}
        payload: dict[str, JsonValue] = {'publication_updates': UpdateHistory(records=records).model_dump(mode='json')}
        if refresh:
            payload.update({'title': record.intent.title, 'body': record.intent.body})
        return self.ledger.checkpoint_run(run.run_id, expected=RunState.PUBLISHED, payload=payload,
            fencing_token=lease.fencing_token, requeue_projection=refresh, actor='reviewed-publication-update')

    def _allowed(self, platform: str) -> None:
        if not publishing_allowed(platform, self.settings()):
            raise ChannelError(platform, 'publishing_disabled', status=403)

    def _credential(self, record: PublicationUpdate) -> Credential:
        credential = self.store.get(record.intent.persona, definition(record.platform).platform, record.intent.account_uid)
        if credential_revision(credential) != record.credential_revision:
            raise ChannelError(record.platform, 'credential_changed', status=409)
        if credential.expires_at is not None and credential.expires_at <= time.time() + 30:
            raise ChannelError(record.platform, 'credential_refresh_required', status=409)
        return credential

    @staticmethod
    def _view(record: PublicationUpdate) -> UpdateView:
        return UpdateView(**record.model_dump(include=set(UpdateView.model_fields)))
