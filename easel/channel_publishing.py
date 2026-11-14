"""Durable, reviewed official-provider publication through Easel's shared ledger."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hmac
from pathlib import Path
import time
from typing import Final

from pydantic import TypeAdapter

from easel._channel_publish_approval import prepare_publication, request_intent, scheduled_timestamp, validate_approval
from easel._channel_publish_media import credential_revision, frozen_paths, media_signature
from easel._channel_publish_models import (ChannelApproval, ChannelIntent, ChannelProgress,
    ChannelPublishRequest, ChannelRunView, ChannelScope, ChannelReconcileRequest, ChannelReconcileView)
from easel._channel_publish_models import ChannelReapproval, ChannelReapproveRequest
from easel._channel_reapproval import ReapprovalSession
from easel._channel_publish_readback import official_readback
from easel._channel_publish_cover import thumbnail_recovery
from easel._channel_publish_queue import PublicationQueue
from easel._channel_handoff import handoff_info
from easel._channel_publish_worker import ChannelWorker, PublicationContext, lease_heartbeat
from easel.channels import ChannelAdapter, ChannelError, CredentialStore, definition
from easel.ownership import MediaManifestEntry, validate_persona
from easel.publish_ledger import PublishLedger
from easel.publish_models import PublishRun, RunState
from easel.publish_recovery import worker_owner
from easel.publish_readback import ReadbackOutcome
from easel.publish_policy import publishing_allowed

_MANIFEST: Final = TypeAdapter(tuple[MediaManifestEntry, ...])


class ChannelPublishing:
    """Compose owned local media, reviewed intent, provider adapters, and fenced persistence."""

    def __init__(self, root: Path, settings: Callable[[], Mapping[str, str]],
                 adapter_factory: Callable[[str], ChannelAdapter] = ChannelAdapter) -> None:
        self.root, self.settings = root, settings
        self.store = CredentialStore(root / '_channels')
        self.adapter_factory = (lambda platform: ChannelAdapter(platform, settings=self.settings(), store=self.store)) if adapter_factory is ChannelAdapter else adapter_factory
        self.ledger = PublishLedger(root / '_publish' / 'ledger.sqlite3')

    def precheck(self, platform: str, intent: ChannelIntent) -> ChannelApproval:
        validate_persona(intent.persona)
        adapter = self.adapter_factory(definition(platform).platform)
        prepared = prepare_publication(self.root, self.settings(), adapter, intent)
        if adapter.profile(prepared.credential).account_uid != intent.account_uid:
            raise ChannelError(platform, 'authenticated_account_mismatch', status=403)
        return prepared.approval

    def publish(self, platform: str, request: ChannelPublishRequest) -> ChannelRunView:
        """Submit a reviewed version once, replaying existing durable identity on retries."""
        self._require_automated(platform)
        validate_persona(request.persona)
        if request.reviewed is not True:
            raise ChannelError(platform, 'explicit_review_required', status=403)
        intent = request_intent(platform, request)
        queue = PublicationQueue(self)
        existing = queue.existing(platform, request)
        if existing is not None:
            if existing.state in {RunState.PUBLISHED, RunState.SUBMITTED, RunState.UNKNOWN, RunState.CANCELLED, RunState.RECONCILING}:
                return self._view(existing)
            if existing.state == RunState.FAILED and not request.retry:
                return self._view(existing)
        adapter = self.adapter_factory(definition(platform).platform)
        if scheduled_timestamp(intent.scheduled_for) > time.time():
            raise ChannelError(platform, 'scheduled_publish_not_due', status=409)
        if existing is not None and existing.state == RunState.QUEUED and existing.payload.get('channel_queued'):
            prepared = queue.snapshot(existing, request)
        else:
            prepared = validate_approval(self.root, self.settings(), adapter, request)
        run = queue.create(request, prepared)
        lease = self.ledger.acquire_lease(run.run_id, worker_owner())
        try:
            with lease_heartbeat(self.ledger, lease):
                current = self.ledger.get_run(run.run_id)
                assert current is not None
                retry_failed = current.state == RunState.FAILED and request.retry
                if current.state in {RunState.PUBLISHED, RunState.SUBMITTED, RunState.UNKNOWN, RunState.CANCELLED, RunState.RECONCILING}:
                    return self._view(current)
                worker = self._worker(adapter, current)
                if current.state == RunState.PREPARED:
                    current = self.ledger.transition(run.run_id, expected=current.state, target=RunState.APPROVED,
                        actor='reviewed-user', fencing_token=lease.fencing_token)
                if current.state in {RunState.APPROVED, RunState.FAILED}:
                    current = self.ledger.transition(run.run_id, expected=current.state, target=RunState.QUEUED,
                        actor='channel-publisher', fencing_token=lease.fencing_token)
                if current.state == RunState.QUEUED:
                    current = self.ledger.transition(run.run_id, expected=current.state, target=RunState.RUNNING,
                        actor='channel-publisher', fencing_token=lease.fencing_token)
                if 'channel_progress' not in current.payload or retry_failed:
                    current = queue.freeze(current, prepared, lease)
                return self._view(worker.execute(current, lease))
        finally:
            self.ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)

    def finalize(self, platform: str, run_id: str, scope: ChannelScope) -> ChannelRunView:
        """Complete only a server-stored pending receipt for the original actor."""
        self._require_automated(platform)
        run = self._owned(platform, run_id, scope)
        if run.state in {RunState.PUBLISHED, RunState.FAILED, RunState.UNKNOWN, RunState.CANCELLED} and not thumbnail_recovery(run):
            return self._view(run)
        if run.state not in {RunState.SUBMITTED, RunState.RUNNING} and not thumbnail_recovery(run):
            raise ChannelError(platform, 'run_not_submitted', status=409)
        if 'channel_progress' not in run.payload:
            raise ChannelError(platform, 'run_not_submitted', status=409)
        adapter = self.adapter_factory(definition(platform).platform)
        worker = self._worker(adapter, run)
        lease = self.ledger.acquire_lease(run_id, worker_owner())
        try:
            with lease_heartbeat(self.ledger, lease):
                current = self._owned(platform, run_id, scope)
                if current.state not in {RunState.SUBMITTED, RunState.RUNNING} and not thumbnail_recovery(current):
                    return self._view(current)
                return self._view(worker.execute(current, lease))
        finally:
            self.ledger.release_lease(run_id, lease.owner, lease.fencing_token)

    def get(self, platform: str, run_id: str, scope: ChannelScope) -> ChannelRunView:
        return self._view(self._owned(platform, run_id, scope))

    def prepare_task(self, platform: str, request: ChannelPublishRequest) -> ChannelRunView:
        return PublicationQueue(self).prepare_task(platform, request)

    def reapprove_precheck(self, platform: str, run_id: str, scope: ChannelScope) -> ChannelReapproval:
        return ReapprovalSession(self).precheck(platform, run_id, scope)

    def reapprove(self, platform: str, run_id: str, request: ChannelReapproveRequest) -> ChannelRunView:
        return ReapprovalSession(self).approve(platform, run_id, request)

    def reconcile(self, platform: str, run_id: str, request: ChannelReconcileRequest) -> ChannelReconcileView:
        """Resolve an uncertain result using authenticated platform evidence under a lease."""
        run = self._owned(platform, run_id, request)
        if run.state not in {RunState.SUBMITTED, RunState.UNKNOWN, RunState.PUBLISHED}:
            raise ChannelError(platform, 'run_not_reconcilable', status=409)
        if run.state == RunState.PUBLISHED:
            return ChannelReconcileView(**self._view(run).model_dump(), verification_outcome='verified')
        adapter = self.adapter_factory(definition(platform).platform)
        credential = self.store.get(run.persona, adapter.definition.platform, run.account_id)
        lease = self.ledger.acquire_lease(run_id, worker_owner())
        try:
            with lease_heartbeat(self.ledger, lease):
                current = self._owned(platform, run_id, request)
                result = official_readback(adapter, credential, current, request)
                if result.outcome == ReadbackOutcome.VERIFIED:
                    current = self.ledger.checkpoint_run(run_id, expected=current.state, target=RunState.PUBLISHED,
                        fencing_token=lease.fencing_token, external_request_id=current.external_request_id,
                        platform_work_id=result.platform_work_id, permalink=result.permalink,
                        payload={'published_at': result.published_at, 'channel_error': ''})
                return ChannelReconcileView(**self._view(current).model_dump(),
                    verification_outcome=result.outcome.value, verification_reason=result.reason)
        finally:
            self.ledger.release_lease(run_id, lease.owner, lease.fencing_token)

    def media_file(self, run_id: str, index: int, persona: str, expires: int, signature: str) -> Path:
        """Authorize one bounded public delivery capability for exact frozen bytes."""
        validate_persona(persona)
        run = self.ledger.get_run(run_id)
        if run is None or run.persona != persona or index < 0 or expires < time.time() or expires > time.time() + 3601:
            raise ChannelError('media', 'media_token_invalid', status=403)
        manifest = (*_MANIFEST.validate_python(run.payload.get('channel_manifest', [])),
                    *_MANIFEST.validate_python(run.payload.get('channel_cover_manifest', [])))
        if index >= len(manifest):
            raise ChannelError('media', 'media_token_invalid', status=403)
        expected = media_signature(self.root, run_id, persona, index, manifest[index].sha256, expires)
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            raise ChannelError('media', 'media_token_invalid', status=403)
        return frozen_paths(self.root, persona, run_id, manifest)[index]

    def _owned(self, platform: str, run_id: str, scope: ChannelScope) -> PublishRun:
        validate_persona(scope.persona)
        run = self.ledger.get_run(run_id)
        if run is None or (run.platform, run.persona, run.account_id) != (platform, scope.persona, scope.account_uid):
            raise ChannelError(platform, 'run_not_found', status=404)
        return run

    def _worker(self, adapter: ChannelAdapter, run: PublishRun) -> ChannelWorker:
        intent = ChannelIntent.model_validate(run.payload['channel_intent'])
        credential = self.store.get(run.persona, adapter.definition.platform, run.account_id)
        if credential_revision(credential) != run.payload['credential_revision']:
            raise ChannelError(run.platform, 'credential_changed', status=409)
        context = PublicationContext(self.root, intent, _MANIFEST.validate_python(run.payload['channel_manifest']),
            credential, lambda: self.store.get(run.persona, adapter.definition.platform, run.account_id), self.settings,
            cover_manifest=_MANIFEST.validate_python(run.payload.get('channel_cover_manifest', [])))
        return ChannelWorker(self.ledger, adapter, context)

    def _require_automated(self, platform: str) -> None:
        if not publishing_allowed(platform, self.settings()):
            raise ChannelError(platform, 'publishing_disabled', status=403)

    @staticmethod
    def _view(run: PublishRun) -> ChannelRunView:
        progress = ChannelProgress.model_validate(run.payload['channel_progress']) if 'channel_progress' in run.payload else None
        details = progress.receipt.details if progress and progress.receipt else {}
        handoff = handoff_info(run.platform, progress.receipt if progress else None)
        return ChannelRunView(run_id=run.run_id, platform=run.platform, persona=run.persona,
            account_uid=run.account_id, state=run.state.value, stage=progress.stage if progress else '',
            platform_work_id=run.platform_work_id, permalink=run.permalink,
            visibility=str(details.get('visibility', '')), work_link_status=str(details.get('work_link_status', '')),
            cover_status=progress.cover_status if progress else '', cover_error=progress.cover_error if progress else '',
            requires_user_action=handoff.required, action_url=handoff.action_url, action_expires_at=handoff.expires_at,
            error=str(run.payload.get('channel_error') or details.get('verification_reason') or ''), created_at=run.created_at, updated_at=run.updated_at)
