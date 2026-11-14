"""Fenced provider stages checkpoint every server receipt before further effects."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import Event, Thread
from typing import Iterator
from typing_extensions import assert_never

from easel._channel_publish_media import credential_revision, frozen_paths, media_base, media_url
from easel._channel_publish_models import ChannelIntent, ChannelProgress
from easel._channel_publish_cover import cover_option, thumbnail_recovery
from easel.channels import ChannelAdapter, ChannelError, Credential, PublishContent, PublishReceipt
from easel.channels.models import MediaInput
from easel.channels.media_metadata import inspect_local_media
from easel.channels.native_definitions import NATIVE_IDS
from easel._channel_handoff import verify_handoff
from easel.publish_policy import publishing_allowed
from easel.ownership import MediaManifestEntry
from easel.publish_ledger import PublishLedger
from easel.publish_models import JsonValue, LeaseRequired, PublishLease, PublishRun, RunState


@contextmanager
def lease_heartbeat(ledger: PublishLedger, lease: PublishLease) -> Iterator[None]:
    """Keep uploads fenced while the synchronous provider streams large media."""
    stopped = Event()
    failures: list[LeaseRequired | sqlite3.Error] = []

    def renew() -> None:
        while not stopped.wait(20):
            try:
                ledger.renew_lease(lease.run_id, lease.owner, lease.fencing_token)
            except (LeaseRequired, sqlite3.Error) as error:
                failures.append(error)
                return

    thread = Thread(target=renew, name='channel-publish-lease', daemon=True)
    thread.start()
    try:
        yield
        if failures:
            raise failures[0]
    finally:
        stopped.set()
        thread.join(timeout=1)


@dataclass(frozen=True, slots=True)
class PublicationContext:
    root: Path
    intent: ChannelIntent
    manifest: tuple[MediaManifestEntry, ...]
    credential: Credential
    current_credential: Callable[[], Credential]
    settings: Callable[[], Mapping[str, str]]
    cover_manifest: tuple[MediaManifestEntry, ...] = ()


class ChannelWorker:
    def __init__(self, ledger: PublishLedger, adapter: ChannelAdapter, context: PublicationContext) -> None:
        self.ledger, self.adapter, self.context = ledger, adapter, context

    def execute(self, run: PublishRun, lease: PublishLease) -> PublishRun:
        """Resume only checkpointed stages, parking ambiguous interrupted mutations."""
        progress = ChannelProgress.model_validate(run.payload['channel_progress'])
        if progress.inflight and not thumbnail_recovery(run):
            return self.ledger.checkpoint_run(run.run_id, expected=run.state, target=RunState.UNKNOWN,
                fencing_token=lease.fencing_token, payload={'channel_error': 'provider_outcome_unknown'})
        current = run
        while progress.stage != 'complete':
            self._authorize()
            self.ledger.renew_lease(run.run_id, lease.owner, lease.fencing_token)
            marked = progress.model_copy(update={'inflight': True})
            current = self._save(current, marked, lease, current.state)
            try:
                next_progress = self._advance(current.run_id, progress)
            except ChannelError as error:
                return self.ledger.checkpoint_run(current.run_id, expected=current.state, target=RunState.UNKNOWN,
                    fencing_token=lease.fencing_token, payload={'channel_error': error.code})
            receipt = next_progress.receipt
            target = RunState.SUBMITTED
            if receipt is not None and receipt.state == 'failed':
                target = RunState.FAILED
            if next_progress.stage == 'complete':
                target = RunState.PUBLISHED
            current = self._save(current, next_progress, lease, target)
            if next_progress.stage in {'publication_poll', 'publication_user_action'} or (next_progress.stage == 'media_poll' and progress.stage == 'media_poll'):
                return current
            if target in {RunState.PUBLISHED, RunState.FAILED}:
                return current
            progress = next_progress
        return current

    def _authorize(self) -> None:
        if not publishing_allowed(self.adapter.definition.platform, self.context.settings()):
            raise ChannelError(self.adapter.definition.platform, 'publishing_disabled', status=403)
        if credential_revision(self.context.current_credential()) != credential_revision(self.context.credential):
            raise ChannelError(self.adapter.definition.platform, 'credential_changed', status=409)

    def _save(self, run: PublishRun, progress: ChannelProgress, lease: PublishLease, target: RunState) -> PublishRun:
        receipt = progress.receipt
        payload: dict[str, JsonValue] = {'channel_progress': progress.model_dump(mode='json')}
        if progress.stage == 'publication_cover' and not run.payload.get('published_at'):
            payload['published_at'] = datetime.now(timezone.utc).isoformat()
        return self.ledger.checkpoint_run(run.run_id, expected=run.state, target=target,
            fencing_token=lease.fencing_token, payload=payload,
            external_request_id=receipt.external_request_id if receipt else '',
            platform_work_id=receipt.platform_work_id if receipt else '', permalink=receipt.permalink if receipt else '')

    def _advance(self, run_id: str, progress: ChannelProgress) -> ChannelProgress:
        context, adapter = self.context, self.adapter
        paths = frozen_paths(context.root, context.intent.persona, run_id, (*context.manifest, *context.cover_manifest))
        receipt = progress.receipt
        match progress.stage:
            case 'media_init':
                result = adapter.initialize_upload(context.credential, paths[progress.media_index], context.intent.media[progress.media_index].kind)
                return progress.model_copy(update={'stage': 'media_upload', 'receipt': result})
            case 'media_upload':
                if receipt is None:
                    raise ChannelError(adapter.definition.platform, 'stored_receipt_missing', status=409)
                result = adapter.upload(context.credential, receipt, paths[progress.media_index])
                return progress.model_copy(update={'stage': 'media_poll', 'receipt': result})
            case 'media_poll':
                if receipt is None:
                    raise ChannelError(adapter.definition.platform, 'stored_receipt_missing', status=409)
                result = adapter.finalize(context.credential, receipt)
                match result.state:
                    case 'requires_finalize':
                        index = progress.media_index + 1
                        return progress.model_copy(update={'stage': 'media_init' if index < len(context.intent.media) else 'publish',
                            'media_index': index, 'uploaded_media': (*progress.uploaded_media, result.external_request_id), 'receipt': None})
                    case 'pending' | 'failed':
                        return progress.model_copy(update={'receipt': result})
                    case 'published' | 'requires_upload' | 'requires_user_action':
                        raise ChannelError(adapter.definition.platform, 'media_receipt_invalid', status=502)
                    case unreachable:
                        assert_never(unreachable)
            case 'publish':
                content = self._content(run_id, progress, paths)
                if adapter.definition.platform in NATIVE_IDS:
                    result = adapter.publish(context.credential, content, media_paths=paths[:len(context.intent.media)],
                        cover_path=paths[-1] if context.cover_manifest else None)
                else:
                    result = adapter.publish(context.credential, content)
                result = self._handoff_receipt(run_id, result)
                match result.state:
                    case 'published':
                        if not self._confirmed(result):
                            raise ChannelError(adapter.definition.platform, 'publish_identity_missing', status=502)
                        return self._published_progress(progress, result)
                    case 'requires_upload':
                        return progress.model_copy(update={'stage': 'publication_upload', 'receipt': result})
                    case 'requires_user_action':
                        return progress.model_copy(update={'stage': 'publication_user_action', 'receipt': result})
                    case 'pending' | 'requires_finalize' | 'failed':
                        return progress.model_copy(update={'stage': 'publication_poll', 'receipt': result})
                    case unreachable:
                        assert_never(unreachable)
            case 'publication_upload' | 'publication_poll' | 'publication_user_action':
                if receipt is None:
                    raise ChannelError(adapter.definition.platform, 'stored_receipt_missing', status=409)
                result = adapter.upload(context.credential, receipt, paths[0]) if progress.stage == 'publication_upload' else adapter.finalize(context.credential, receipt)
                result = self._handoff_receipt(run_id, result)
                match result.state:
                    case 'published':
                        if not self._confirmed(result):
                            raise ChannelError(adapter.definition.platform, 'publish_identity_missing', status=502)
                        return self._published_progress(progress, result)
                    case 'pending' | 'requires_finalize' | 'failed':
                        return progress.model_copy(update={'stage': 'publication_poll', 'receipt': result})
                    case 'requires_upload':
                        raise ChannelError(adapter.definition.platform, 'publication_receipt_invalid', status=502)
                    case 'requires_user_action':
                        return progress.model_copy(update={'stage': 'publication_user_action', 'receipt': result})
                    case unreachable:
                        assert_never(unreachable)
            case 'publication_cover':
                if receipt is None or not receipt.platform_work_id or not context.cover_manifest:
                    raise ChannelError(adapter.definition.platform, 'stored_cover_receipt_missing', status=409)
                try:
                    result = adapter.apply_cover(context.credential, receipt, paths[-1])
                except ChannelError as error:
                    return progress.model_copy(update={'stage': 'complete', 'inflight': False,
                        'cover_status': 'failed', 'cover_error': error.code})
                return progress.model_copy(update={'stage': 'complete', 'receipt': result,
                    'cover_status': 'applied', 'cover_error': '', 'inflight': False})
            case 'complete':
                return progress
            case unreachable:
                assert_never(unreachable)

    def _published_progress(self, progress: ChannelProgress, receipt: PublishReceipt) -> ChannelProgress:
        cover = self.adapter.definition.platform == 'youtube' and bool(self.context.cover_manifest)
        status = 'applied' if self.context.cover_manifest else progress.cover_status
        return progress.model_copy(update={'stage': 'publication_cover' if cover else 'complete',
            'receipt': receipt, 'cover_status': 'pending' if cover else status})

    def _handoff_receipt(self, run_id: str, receipt: PublishReceipt) -> PublishReceipt:
        run = self.ledger.get_run(run_id)
        assert run is not None
        return verify_handoff(self.adapter, self.context.credential, run, receipt)

    def _confirmed(self, receipt: PublishReceipt) -> bool:
        return bool(receipt.platform_work_id) or (self.adapter.definition.platform == 'tiktok'
            and self.context.intent.options.get('privacy_level') == 'SELF_ONLY'
            and bool(receipt.external_request_id) and receipt.details.get('visibility') == 'private'
            and receipt.details.get('work_link_status') == 'pending'
            and receipt.details.get('provider_status') == 'PUBLISH_COMPLETE')

    def _content(self, run_id: str, progress: ChannelProgress, paths: tuple[Path, ...]) -> PublishContent:
        context = self.context
        media: list[MediaInput] = []
        for index, item in enumerate(context.intent.media):
            metadata = inspect_local_media(paths[index])
            if index < len(progress.uploaded_media):
                media.append(MediaInput(kind=item.kind, media_id=progress.uploaded_media[index], metadata=metadata))
            elif self.adapter.definition.platform in {'youtube', 'wechat-official-open'} or (self.adapter.definition.platform in {'tiktok', 'facebook'} and item.kind == 'video'):
                media.append(MediaInput(kind=item.kind, metadata=metadata))
            else:
                url = media_url(context.root, media_base(dict(context.settings())), run_id,
                                context.intent.persona, index, context.manifest[index].sha256)
                media.append(MediaInput(kind=item.kind, url=url, metadata=metadata))
        options = dict(context.intent.options)
        for name in ('image_url',):
            if name in options:
                index = next(index for index, item in enumerate(context.intent.media) if item.path == options[name])
                options[name] = media_url(context.root, media_base(context.settings()), run_id,
                    context.intent.persona, index, context.manifest[index].sha256)
        if context.cover_manifest and cover_option(self.adapter.definition.platform):
            options[cover_option(self.adapter.definition.platform)] = media_url(context.root,
                media_base(context.settings()), run_id, context.intent.persona,
                len(context.manifest), context.cover_manifest[0].sha256)
        if self.adapter.definition.platform in {'youtube', 'tiktok', 'facebook', 'bilibili-open', 'kwai-open'} and context.manifest:
            options['content_length'] = context.manifest[0].size
        if self.adapter.definition.platform == 'tiktok' and context.intent.media[0].kind == 'video':
            options['source'] = 'FILE_UPLOAD'
        return PublishContent(title=context.intent.title, body=context.intent.body, media=tuple(media), options=options)
