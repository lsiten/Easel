"""Freeze reviewed scheduled publications before their source library can change."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
import hmac
from pathlib import Path
import time
from typing import Protocol

from pydantic import TypeAdapter

from easel._channel_publish_approval import PreparedPublication, request_intent, validate_approval
from easel._channel_publish_cover import normalize_cover_intent
from easel._channel_publish_media import credential_revision, frozen_paths
from easel._channel_publish_models import ChannelApproval, ChannelIntent, ChannelProgress, ChannelPublishRequest, ChannelRunView
from easel._channel_publish_worker import lease_heartbeat
from easel.channels import ChannelAdapter, ChannelError, CredentialStore, definition
from easel.channels.native_config import native_setting
from easel.ownership import MediaManifestEntry, freeze_media, validate_persona
from easel.publish_ledger import PublishLedger
from easel.publish_models import IdempotencyConflict, PublishLease, PublishRun, RunState
from easel.publish_recovery import worker_owner


class QueueHost(Protocol):
    root: Path
    ledger: PublishLedger
    store: CredentialStore
    settings: Callable[[], Mapping[str, str]]
    adapter_factory: Callable[[str], ChannelAdapter]

    def _require_automated(self, platform: str) -> None: ...
    def _view(self, run: PublishRun) -> ChannelRunView: ...


class PublicationQueue:
    def __init__(self, host: QueueHost) -> None:
        self.host = host

    def existing(self, platform: str, request: ChannelPublishRequest) -> PublishRun | None:
        intent = request_intent(platform, request)
        run = next((candidate for candidate in self.host.ledger.list_runs(persona=intent.persona, platform=platform)
            if request.idempotency_key in {candidate.idempotency_key, candidate.run_id}), None)
        if run is not None:
            stored = normalize_cover_intent(platform, ChannelIntent.model_validate(run.payload.get('channel_intent')))
            if run.account_id != intent.account_uid or stored.model_dump(mode='json') != intent.model_dump(mode='json'):
                raise IdempotencyConflict('发布 key 已绑定其他内容或账号')
        return run

    def create(self, request: ChannelPublishRequest, prepared: PreparedPublication, *, queued: bool = False) -> PublishRun:
        intent = prepared.intent
        return self.host.ledger.create_run(platform=prepared.credential.platform, persona=intent.persona,
            account_id=intent.account_uid, idempotency_key=request.idempotency_key,
            approval_digest=prepared.identity_digest, media_manifest_hash=prepared.approval.media_manifest_hash,
            payload={'channel_intent': intent.model_dump(mode='json'),
                'channel_manifest': [asdict(item) for item in prepared.manifest],
                'channel_cover_manifest': [asdict(item) for item in prepared.cover_manifest],
                'credential_revision': credential_revision(prepared.credential), 'title': intent.title,
                'body': intent.body, 'platform_name': prepared.credential.platform, 'publish_type': 'official_api',
                'account_uid': intent.account_uid, 'context': intent.context.model_dump(mode='json'),
                'app_client_id': native_setting(self.host.settings(), prepared.credential.platform, 'CLIENT_ID'),
                'channel_queued': queued, 'channel_approval': prepared.approval.model_dump(mode='json')})

    def freeze(self, run: PublishRun, prepared: PreparedPublication, lease: PublishLease) -> PublishRun:
        paths = freeze_media(self.host.root, run.persona, run.run_id, (*prepared.manifest, *prepared.cover_manifest))
        upload_media = bool(prepared.intent.media) and (run.platform in {'linkedin', 'twitter', 'bilibili-open', 'kwai-open'} or
            (run.platform == 'pinterest' and prepared.intent.media[0].kind == 'video'))
        progress = ChannelProgress(stage='media_init' if upload_media else 'publish',
            frozen_paths=tuple(path.relative_to(self.host.root).as_posix() for path in paths))
        return self.host.ledger.checkpoint_run(run.run_id, expected=run.state, fencing_token=lease.fencing_token,
            payload={'channel_progress': progress.model_dump(mode='json'), 'channel_error': ''})

    def snapshot(self, run: PublishRun, request: ChannelPublishRequest) -> PreparedPublication:
        """Authenticate dispatch against the reviewed frozen snapshot without reopening originals."""
        approval = ChannelApproval.model_validate(run.payload['channel_approval'])
        effective = ChannelApproval.model_validate(run.payload.get('channel_dispatch_approval', run.payload['channel_approval']))
        if effective.approval_expires_at <= time.time():
            raise ChannelError(run.platform, 'approval_expired', status=409)
        if request.approval_expires_at != approval.approval_expires_at or not hmac.compare_digest(request.approval_digest.encode(), approval.approval_digest.encode()):
            raise ChannelError(run.platform, 'approval_changed', status=409)
        platform = definition(run.platform).platform
        credential = self.host.store.get(run.persona, platform, run.account_id)
        if credential_revision(credential) != run.payload['credential_revision']:
            raise ChannelError(platform, 'credential_changed', status=409)
        if credential.expires_at is not None and credential.expires_at <= time.time() + 30:
            raise ChannelError(platform, 'credential_refresh_required', status=409)
        manifest = TypeAdapter(tuple[MediaManifestEntry, ...]).validate_python(run.payload['channel_manifest'])
        covers = TypeAdapter(tuple[MediaManifestEntry, ...]).validate_python(run.payload.get('channel_cover_manifest', []))
        frozen_paths(self.host.root, run.persona, run.run_id, (*manifest, *covers))
        return PreparedPublication(ChannelIntent.model_validate(run.payload['channel_intent']), credential,
            manifest, covers, approval, run.approval_digest)

    def prepare_task(self, platform: str, request: ChannelPublishRequest) -> ChannelRunView:
        self.host._require_automated(platform)
        validate_persona(request.persona)
        if request.reviewed is not True:
            raise ChannelError(platform, 'explicit_review_required', status=403)
        existing = self.existing(platform, request)
        if existing is not None and existing.payload.get('channel_queued') and existing.state == RunState.QUEUED:
            self.snapshot(existing, request)
            return self.host._view(existing)
        if existing is not None and existing.state not in {RunState.PREPARED, RunState.APPROVED}:
            return self.host._view(existing)
        adapter = self.host.adapter_factory(definition(platform).platform)
        prepared = validate_approval(self.host.root, self.host.settings(), adapter, request)
        run = self.create(request, prepared, queued=True)
        lease = self.host.ledger.acquire_lease(run.run_id, worker_owner())
        try:
            with lease_heartbeat(self.host.ledger, lease):
                current = self.host.ledger.get_run(run.run_id)
                assert current is not None
                if current.state == RunState.QUEUED:
                    self.snapshot(current, request)
                    return self.host._view(current)
                if current.state not in {RunState.PREPARED, RunState.APPROVED}:
                    return self.host._view(current)
                if 'channel_progress' not in current.payload:
                    current = self.freeze(current, prepared, lease)
                if current.state == RunState.PREPARED:
                    current = self.host.ledger.transition(run.run_id, expected=current.state, target=RunState.APPROVED,
                        actor='reviewed-user', fencing_token=lease.fencing_token)
                current = self.host.ledger.transition(run.run_id, expected=current.state, target=RunState.QUEUED,
                    actor='scheduled-task', fencing_token=lease.fencing_token)
                return self.host._view(current)
        finally:
            self.host.ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)
