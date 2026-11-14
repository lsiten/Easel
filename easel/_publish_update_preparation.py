"""Validate desired edits against fresh authenticated work metadata."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Mapping

from pydantic import JsonValue

from easel._channel_publish_cover import cover_manifest
from easel._channel_publish_media import approval_signature, credential_revision
from easel._channel_publish_models import ChannelIntent
from easel.channels import ChannelAdapter, ChannelError, Credential
from easel.channels.options import provider_options
from easel.channels.reads import _document
from easel.ownership import MediaManifestEntry
from easel.publish_models import PublishRun, digest_mapping
from easel.publish_update_models import UpdateApproval, UpdateIntent


@dataclass(frozen=True, slots=True)
class PreparedUpdate:
    intent: UpdateIntent
    cover_manifest: tuple[MediaManifestEntry, ...]
    approval: UpdateApproval
    credential_revision: str


def editable_work(platform: str, work: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Keep metrics outside the revision checked for a metadata edit."""
    if platform == 'youtube':
        snippet, status = _document(work.get('snippet')), _document(work.get('status'))
        return {'id': work.get('id'), 'etag': work.get('etag'),
            'snippet': {key: value for key, value in snippet.items() if key in {
                'title', 'description', 'tags', 'categoryId', 'defaultLanguage', 'defaultAudioLanguage'}},
            'status': {key: value for key, value in status.items() if key in {
                'privacyStatus', 'license', 'embeddable', 'selfDeclaredMadeForKids', 'containsSyntheticMedia', 'publishAt'}}}
    return {key: value for key, value in work.items() if key in {'id', 'title', 'description', 'link', 'board_id', 'alt_text'}}


def normalize_update(platform: str, intent: UpdateIntent) -> UpdateIntent:
    options = provider_options(platform, intent.options)
    allowed = {'youtube': {'privacyStatus', 'categoryId', 'publishAt', 'license', 'embeddable',
        'selfDeclaredMadeForKids', 'madeForKids', 'containsSyntheticMedia'}, 'pinterest': {'boardId', 'link'}}
    if platform not in allowed:
        raise ChannelError(platform, 'update_unsupported', status=405)
    if set(options) - allowed[platform]:
        raise ChannelError(platform, 'update_option_unsupported')
    if platform == 'youtube' and not intent.title.strip():
        raise ChannelError(platform, 'title_required')
    if platform == 'pinterest' and len(intent.body) > 800:
        raise ChannelError(platform, 'body_too_long')
    if intent.cover_path and platform != 'youtube':
        raise ChannelError(platform, 'update_cover_unsupported')
    return intent.model_copy(update={'options': options})


def prepare_update(root: Path, adapter: ChannelAdapter, credential: Credential, run: PublishRun,
                   task_id: str, intent: UpdateIntent, expires_at: int = 0) -> PreparedUpdate:
    platform = adapter.definition.platform
    normalized = normalize_update(platform, intent)
    if credential.expires_at is not None and credential.expires_at <= time.time() + 30:
        raise ChannelError(platform, 'credential_refresh_required', status=409)
    if adapter.profile(credential).account_uid != run.account_id:
        raise ChannelError(platform, 'authenticated_account_mismatch', status=403)
    work = adapter.work(credential, run.platform_work_id)
    if str(work.get('id') or '') != run.platform_work_id:
        raise ChannelError(platform, 'work_identity_mismatch', status=403)
    current = editable_work(platform, work)
    if platform == 'youtube' and (not isinstance(current.get('etag'), str) or not current['etag']):
        raise ChannelError(platform, 'work_revision_unavailable', status=409)
    revision = digest_mapping(current)
    covers = cover_manifest(root, platform, ChannelIntent(persona=intent.persona,
        account_uid=intent.account_uid, cover_path=intent.cover_path)) if intent.cover_path else ()
    expiry = expires_at or int(time.time()) + 1800
    if expiry <= time.time() or expiry > time.time() + 1801:
        raise ChannelError(platform, 'approval_expired', status=409)
    credential_hash = credential_revision(credential)
    identity = digest_mapping({'run_id': run.run_id, 'task_id': task_id, 'platform': platform,
        'platform_work_id': run.platform_work_id, 'intent': normalized.model_dump(mode='json'),
        'viewed_revision': revision, 'credential_revision': credential_hash,
        'cover_hash': covers[0].sha256 if covers else ''})
    signature = approval_signature(root, json.dumps(['publication-update', identity, expiry], separators=(',', ':')))
    approval = UpdateApproval(task_id=task_id, run_id=run.run_id, platform=platform,
        platform_work_id=run.platform_work_id, current=current, viewed_revision=revision,
        approval_digest=signature, approval_expires_at=expiry, cover_sha256=covers[0].sha256 if covers else '')
    return PreparedUpdate(normalized, covers, approval, credential_hash)
