"""Fresh human review of an existing pending artifact after credential rotation."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hmac
import json
from pathlib import Path
import time
from typing import Protocol

from pydantic import TypeAdapter

from easel._channel_publish_media import approval_signature, credential_revision, frozen_paths, media_url
from easel._channel_publish_approval import scheduled_timestamp
from easel._channel_publish_models import (ChannelApproval, ChannelIntent, ChannelProgress, ChannelReapproval,
    ChannelReapproveRequest, ChannelRunView, ChannelScope, ReviewedMedia)
from easel._channel_publish_worker import ChannelWorker, lease_heartbeat
from easel.channels import ChannelAdapter, ChannelError, CredentialStore, definition
from easel.manual_publish import publishing_mode
from easel.ownership import MediaManifestEntry
from easel.publish_ledger import PublishLedger
from easel.publish_models import PublishRun, RunState, digest_mapping
from easel.publish_recovery import worker_owner
from easel.publish_policy import publishing_allowed
from easel._channel_queued_tasks import restore_reviewed_task


class ReapprovalHost(Protocol):
    root: Path
    ledger: PublishLedger
    store: CredentialStore
    adapter_factory: Callable[[str], ChannelAdapter]
    settings: Callable[[], Mapping[str, str]]

    def _owned(self, platform: str, run_id: str, scope: ChannelScope) -> PublishRun: ...
    def _worker(self, adapter: ChannelAdapter, run: PublishRun) -> ChannelWorker: ...
    def _view(self, run: PublishRun) -> ChannelRunView: ...


class ReapprovalSession:
    def __init__(self, host: ReapprovalHost) -> None:
        self.host = host

    def precheck(self, platform: str, run_id: str, scope: ChannelScope) -> ChannelReapproval:
        run = self.host._owned(platform, run_id, scope)
        return self._prepare(run)

    def approve(self, platform: str, run_id: str, request: ChannelReapproveRequest) -> ChannelRunView:
        if request.reviewed is not True:
            raise ChannelError(platform, 'explicit_review_required', status=403)
        if not publishing_allowed(platform, self.host.settings()):
            raise ChannelError(platform, 'publishing_disabled', status=403)
        self.host._owned(platform, run_id, request)
        lease = self.host.ledger.acquire_lease(run_id, worker_owner())
        try:
            with lease_heartbeat(self.host.ledger, lease):
                run = self.host._owned(platform, run_id, request)
                approval = self._prepare(run, request.approval_expires_at)
                credential = self.host.store.get(run.persona, definition(platform).platform, run.account_id)
                revision = credential_revision(credential)
                previous = run.payload.get('channel_dispatch_approval')
                if run.state == RunState.QUEUED and previous is not None:
                    recorded = ChannelApproval.model_validate(previous)
                    if (hmac.compare_digest(request.approval_digest.encode(), recorded.approval_digest.encode()) and
                            request.approval_expires_at == recorded.approval_expires_at and
                            revision == run.payload['credential_revision']):
                        restore_reviewed_task(self.host.root, run)
                        return self.host._view(run)
                if not hmac.compare_digest(request.approval_digest.encode(), approval.approval_digest.encode()):
                    raise ChannelError(platform, 'approval_changed', status=409)
                if self._signature(run, revision, request.approval_expires_at) != request.approval_digest:
                    raise ChannelError(platform, 'credential_changed', status=409)
                payload = {'credential_revision': revision, 'channel_error': '',
                    'channel_reapproval': {'digest': request.approval_digest, 'expires_at': request.approval_expires_at}}
                if run.state == RunState.QUEUED:
                    payload['channel_dispatch_approval'] = approval.model_dump(mode='json', exclude={'run_id', 'stage', 'original_intent', 'frozen_media'})
                current = self.host.ledger.checkpoint_run(run_id, expected=run.state,
                    fencing_token=lease.fencing_token, actor='reviewed-user',
                    payload=payload)
                if current.state == RunState.QUEUED:
                    restore_reviewed_task(self.host.root, current)
                    return self.host._view(current)
                adapter = self.host.adapter_factory(platform)
                return self.host._view(self.host._worker(adapter, current).execute(current, lease))
        finally:
            self.host.ledger.release_lease(run_id, lease.owner, lease.fencing_token)

    def _prepare(self, run: PublishRun, expires_at: int = 0) -> ChannelReapproval:
        progress = ChannelProgress.model_validate(run.payload.get('channel_progress'))
        queued = run.state == RunState.QUEUED and run.payload.get('channel_queued') is True
        if (run.state != RunState.SUBMITTED and not queued) or progress.inflight:
            raise ChannelError(run.platform, 'pending_artifact_required', status=409)
        intent = ChannelIntent.model_validate(run.payload['channel_intent'])
        manifest = TypeAdapter(tuple[MediaManifestEntry, ...]).validate_python(run.payload['channel_manifest'])
        covers = TypeAdapter(tuple[MediaManifestEntry, ...]).validate_python(run.payload.get('channel_cover_manifest', []))
        frozen_paths(self.host.root, run.persona, run.run_id, (*manifest, *covers))
        platform = definition(run.platform).platform
        credential = self.host.store.get(run.persona, platform, run.account_id)
        if credential.expires_at is not None and credential.expires_at <= time.time() + 30:
            raise ChannelError(platform, 'credential_refresh_required', status=409)
        if self.host.adapter_factory(platform).profile(credential).account_uid != run.account_id:
            raise ChannelError(platform, 'authenticated_account_mismatch', status=403)
        latest = max(time.time() + 1800, scheduled_timestamp(intent.scheduled_for) + 1800) if queued else time.time() + 1800
        expiry = expires_at or int(latest)
        if expiry <= time.time() or expiry > latest + 1 or expiry > time.time() + 30 * 86400 + 1801:
            raise ChannelError(platform, 'approval_expired', status=409)
        approval = ChannelApproval(platform=platform, persona=run.persona, account_uid=run.account_id,
            mode=publishing_mode(self.host.settings()), approval_digest=self._signature(run, credential_revision(credential), expiry),
            approval_expires_at=expiry, media_manifest_hash=run.media_manifest_hash)
        media = tuple(ReviewedMedia(kind=item.kind, sha256=entry.sha256, size=entry.size,
            preview_url=media_url(self.host.root, '', run.run_id, run.persona, index, entry.sha256))
            for index, (item, entry) in enumerate(zip(intent.media, manifest)))
        media += tuple(ReviewedMedia(kind='image', role='cover', sha256=entry.sha256, size=entry.size,
            preview_url=media_url(self.host.root, '', run.run_id, run.persona, len(manifest) + index, entry.sha256))
            for index, entry in enumerate(covers))
        return ChannelReapproval(**approval.model_dump(), run_id=run.run_id, stage=progress.stage,
                                 original_intent=intent, frozen_media=media)

    def _signature(self, run: PublishRun, revision: str, expires_at: int) -> str:
        message = digest_mapping({'run_id': run.run_id, 'generation': run.generation,
            'intent': run.payload['channel_intent'], 'manifest_hash': run.media_manifest_hash,
            'progress': run.payload['channel_progress'], 'credential_revision': revision,
            'mode': publishing_mode(self.host.settings())})
        return approval_signature(self.host.root, json.dumps(['reapprove', message, expires_at], separators=(',', ':')))
