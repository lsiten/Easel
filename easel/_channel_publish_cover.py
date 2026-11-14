"""Source-supported owned cover inputs and video-thumbnail recovery identities."""
from __future__ import annotations

from pathlib import Path

from easel._channel_publish_models import ChannelIntent, ChannelProgress
from easel.channels import ChannelError, definition
from easel.channels.rules import validate_local_cover, validate_local_media
from easel.ownership import MediaManifestEntry, build_media_manifest
from easel.publish_models import PublishRun
from typing_extensions import assert_never


def normalize_cover_intent(platform: str, intent: ChannelIntent) -> ChannelIntent:
    """Normalize local cover aliases before review and reject unsupported provider modes."""
    options = dict(intent.options)
    cover = intent.cover_path
    aliases = {'instagram': ('cover_url',), 'pinterest': ('coverImageUrl', 'cover_image_url'), 'kwai-open': ('cover',)}.get(platform, ())
    for name in ('cover_url', 'coverImageUrl', 'cover_image_url', 'cover'):
        if name not in options:
            continue
        if name not in aliases:
            raise ChannelError(platform, 'cover_option_unsupported')
        value = options.pop(name)
        if not isinstance(value, str) or not value or (cover and cover != value):
            raise ChannelError(platform, 'cover_path_conflict')
        cover = value
    if not cover and platform == 'wechat-official-open':
        cover = next((item.path for item in intent.media if item.kind == 'image'), '')
    if not cover and platform in {'kwai-open', 'wechat-official-open'}:
        raise ChannelError(platform, 'cover_required')
    if cover:
        video = len(intent.media) == 1 and intent.media[0].kind == 'video'
        match definition(platform).platform:
            case 'youtube' | 'instagram' | 'pinterest' | 'bilibili-open' | 'kwai-open':
                if not video:
                    raise ChannelError(platform, 'cover_requires_video')
            case 'tiktok':
                candidates = [item.path for item in intent.media if item.kind == 'image']
                if cover not in candidates or len(candidates) != len(intent.media):
                    raise ChannelError(platform, 'cover_must_reference_published_photo')
                options.setdefault('photo_cover_index', candidates.index(cover))
            case 'wechat-official-open':
                pass
            case 'facebook' | 'threads' | 'twitter' | 'linkedin' | 'google-business' | 'douyin-open' | 'wechat-channels-open' | 'rednote-link':
                raise ChannelError(platform, 'cover_unsupported')
            case unreachable:
                assert_never(unreachable)
    return intent.model_copy(update={'cover_path': cover, 'options': options})


def cover_manifest(root: Path, platform: str, intent: ChannelIntent) -> tuple[MediaManifestEntry, ...]:
    if not intent.cover_path:
        return ()
    entries = build_media_manifest(root, intent.persona, [intent.cover_path])
    entry = entries[0]
    if entry.size <= 0:
        raise ChannelError(platform, 'cover_empty')
    path = root / entry.relative_path
    match definition(platform).platform:
        case 'youtube' | 'instagram' | 'pinterest' | 'bilibili-open' | 'kwai-open' | 'wechat-official-open':
            validate_local_cover(platform, path)
        case 'tiktok':
            validate_local_media(platform, path, 'image', intent.options)
        case 'facebook' | 'threads' | 'twitter' | 'linkedin' | 'google-business' | 'douyin-open' | 'wechat-channels-open' | 'rednote-link':
            raise ChannelError(platform, 'cover_unsupported')
        case unreachable:
            assert_never(unreachable)
    return entries


def cover_option(platform: str) -> str:
    return {'instagram': 'cover_url', 'pinterest': 'coverImageUrl'}.get(platform, '')


def thumbnail_recovery(run: PublishRun) -> bool:
    if run.platform != 'youtube' or not run.platform_work_id or 'channel_progress' not in run.payload:
        return False
    progress = ChannelProgress.model_validate(run.payload['channel_progress'])
    return progress.stage == 'publication_cover' and progress.receipt is not None and progress.receipt.platform_work_id == run.platform_work_id
