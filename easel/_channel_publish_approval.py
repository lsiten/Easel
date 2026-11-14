"""Validate local publication intent and bind its review to account authorization."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import hmac
from pathlib import Path
import time

from easel._channel_publish_media import approval_signature, credential_revision, media_base
from easel._channel_publish_models import ChannelApproval, ChannelIntent, ChannelPublishRequest
from easel._channel_publish_cover import cover_manifest, cover_option, normalize_cover_intent
from easel.channels import ChannelAdapter, ChannelError, Credential, CredentialStore, PublishContent
from easel.channels.models import MediaInput
from easel.channels.options import provider_options
from easel.channels.publish import validate_content
from easel.channels.rules import validate_local_media
from easel.manual_publish import publishing_mode
from easel.ownership import MediaManifestEntry, build_media_manifest
from easel.publish_models import digest_mapping
from easel.publish_context import validate_context


@dataclass(frozen=True, slots=True)
class PreparedPublication:
    intent: ChannelIntent
    credential: Credential
    manifest: tuple[MediaManifestEntry, ...]
    cover_manifest: tuple[MediaManifestEntry, ...]
    approval: ChannelApproval
    identity_digest: str


def needs_public_media(platform: str, intent: ChannelIntent) -> bool:
    return bool(intent.media) and (platform in {'instagram', 'threads', 'douyin-open', 'wechat-channels-open', 'rednote-link'} or
        (platform in {'tiktok', 'pinterest', 'facebook'} and intent.media[0].kind == 'image'))


def scheduled_timestamp(value: str) -> float:
    if not value:
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ChannelError('publish', 'schedule_invalid') from error
    if parsed.tzinfo is None:
        raise ChannelError('publish', 'schedule_timezone_required')
    stamp = parsed.astimezone(timezone.utc).timestamp()
    if stamp > time.time() + 30 * 86400:
        raise ChannelError('publish', 'schedule_too_far')
    return stamp


def prepare_publication(root: Path, settings: Mapping[str, str], adapter: ChannelAdapter,
                        intent: ChannelIntent, expires_at: int = 0) -> PreparedPublication:
    """Parse all publication content before creating a durable run or remote mutation."""
    platform = adapter.definition.platform
    intent = normalize_cover_intent(platform, intent)
    validate_context(root, intent.persona, intent.context)
    credential = CredentialStore(root / '_channels').get(intent.persona, platform, intent.account_uid)
    if credential.expires_at is not None and credential.expires_at <= time.time() + 30:
        raise ChannelError(platform, 'credential_refresh_required', status=409)
    manifest = build_media_manifest(root, intent.persona, [item.path for item in intent.media])
    covers = cover_manifest(root, platform, intent)
    if len(manifest) != len(intent.media):
        raise ChannelError(platform, 'duplicate_media')
    metadata = []
    for item, entry in zip(intent.media, manifest):
        metadata.append(validate_local_media(platform, root / entry.relative_path, item.kind, intent.options))
        if entry.size <= 0:
            raise ChannelError(platform, 'media_empty')
    options = dict(intent.options)
    for name in ('image_url',):
        if name in options:
            if options[name] not in [item.path for item in intent.media]:
                raise ChannelError(platform, 'owned_cover_media_required')
            options[name] = 'https://approved.invalid/owned-media'
    if covers and cover_option(platform):
        options[cover_option(platform)] = 'https://approved.invalid/owned-cover'
    if platform in {'youtube', 'tiktok', 'facebook', 'bilibili-open', 'kwai-open'} and manifest:
        options['content_length'] = manifest[0].size
    if platform == 'tiktok' and intent.media and intent.media[0].kind == 'video':
        options['source'] = 'FILE_UPLOAD'
    normalized = provider_options(platform, options)
    if platform == 'facebook' and not credential.parent_uid:
        raise ChannelError(platform, 'page_account_required')
    if platform == 'pinterest' and not normalized.get('boardId'):
        raise ChannelError(platform, 'board_id_required')
    if platform == 'tiktok' and not normalized.get('privacy_level'):
        raise ChannelError(platform, 'privacy_level_required')
    if needs_public_media(platform, intent) or (covers and cover_option(platform)) or 'image_url' in intent.options:
        media_base(dict(settings))
    content = PublishContent(title=intent.title, body=intent.body, options=normalized,
        media=tuple(MediaInput(kind=item.kind, url='https://approved.invalid/owned-media', metadata=metadata[index])
                    for index, item in enumerate(intent.media)))
    validate_content(adapter.reader, content)
    scheduled = scheduled_timestamp(intent.scheduled_for)
    maximum = int(max(time.time() + 1800, scheduled + 1800))
    expiry = expires_at or maximum
    if expiry < int(time.time()) or expiry > maximum + 1:
        raise ChannelError(platform, 'approval_expired', status=409)
    identity_media = {'media': [asdict(entry) for entry in manifest]}
    if covers:
        identity_media['cover'] = [asdict(entry) for entry in covers]
    media_hash = digest_mapping(identity_media)
    identity = digest_mapping({'platform': platform, 'intent': intent.model_dump(mode='json'),
        'credential_revision': credential_revision(credential), 'media_hash': media_hash,
        'mode': publishing_mode(settings)})
    signature = approval_signature(root, json.dumps([identity, expiry], separators=(',', ':')))
    approval = ChannelApproval(platform=platform, persona=intent.persona, account_uid=intent.account_uid,
        mode=publishing_mode(settings), approval_digest=signature, approval_expires_at=expiry,
        media_manifest_hash=media_hash)
    return PreparedPublication(intent, credential, manifest, covers, approval, identity)


def validate_approval(root: Path, settings: Mapping[str, str], adapter: ChannelAdapter,
                      request: ChannelPublishRequest) -> PreparedPublication:
    """Verify a reviewed request for direct dispatch or durable flow creation."""
    if request.reviewed is not True:
        raise ChannelError(adapter.definition.platform, 'explicit_review_required', status=403)
    intent = ChannelIntent.model_validate(request.model_dump(exclude={
        'reviewed', 'approval_digest', 'approval_expires_at', 'idempotency_key', 'retry'}))
    prepared = prepare_publication(root, settings, adapter, intent, request.approval_expires_at)
    if not hmac.compare_digest(request.approval_digest.encode(), prepared.approval.approval_digest.encode()):
        raise ChannelError(adapter.definition.platform, 'approval_changed', status=409)
    return prepared


def request_intent(platform: str, request: ChannelPublishRequest) -> ChannelIntent:
    return normalize_cover_intent(platform, ChannelIntent.model_validate(request.model_dump(exclude={
        'reviewed', 'approval_digest', 'approval_expires_at', 'idempotency_key', 'retry'})))
