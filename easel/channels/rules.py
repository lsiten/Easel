"""Source-declared formats, sizes, durations, and optional aspect metadata."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue

from easel.channels.models import ChannelError, MediaMetadata
from easel.channels.media_metadata import inspect_local_media

_MIB = 1024**2
_FORMATS = {
    'youtube': ('', 'mp4 mov avi wmv flv mkv'), 'tiktok': ('jpg jpeg webp', 'mp4 mov webm'),
    'instagram': ('jpg jpeg', 'mp4 mov'), 'facebook': ('jpg jpeg png gif bmp tiff', 'mp4 mov avi'),
    'threads': ('jpg jpeg png', 'mp4 mov'), 'twitter': ('jpg jpeg png gif webp', 'mp4 mov'),
    'linkedin': ('jpg jpeg png gif', 'mp4'), 'pinterest': ('bmp jpg jpeg png tiff webp', 'mp4 m4v mov'),
}
_IMAGE_SIZE = {'tiktok': 20, 'instagram': 8, 'facebook': 10, 'threads': 8, 'twitter': 5, 'linkedin': 10, 'pinterest': 20}
_VIDEO_SIZE = {'youtube': 256 * 1024, 'tiktok': 4 * 1024, 'instagram': 1024, 'facebook': 1024, 'threads': 1024, 'twitter': 512, 'linkedin': 200}
_VIDEO_DURATION = {'youtube': 43200, 'tiktok': 600, 'instagram': 900, 'facebook': 3600, 'threads': 300, 'twitter': 140, 'linkedin': 600, 'pinterest': 300}
_FORMATS.update({'bilibili-open': ('jpg jpeg png', 'mp4 flv'), 'kwai-open': ('jpg jpeg png bmp webp', 'mp4 avi mkv mov webm'),
    'douyin-open': ('jpg jpeg png bmp', 'mp4 avi mkv mov'), 'wechat-official-open': ('jpg jpeg png gif bmp', ''),
    'wechat-channels-open': ('jpg jpeg png bmp', 'mp4 avi mkv mov flv rmvb wmv webm'), 'rednote-link': ('jpg jpeg png bmp webp', 'mp4 avi mkv mov webm')})
_IMAGE_SIZE.update({'bilibili-open': 5, 'kwai-open': 20, 'douyin-open': 20, 'wechat-official-open': 10, 'wechat-channels-open': 20, 'rednote-link': 20})
_VIDEO_SIZE.update({'bilibili-open': 4096, 'kwai-open': 100, 'douyin-open': 100, 'wechat-channels-open': 1024, 'rednote-link': 1024})
_VIDEO_DURATION.update({'bilibili-open': 18000, 'kwai-open': 1800, 'douyin-open': 900, 'wechat-channels-open': 3600, 'rednote-link': 3600})


def declared_media_rules(platform: str) -> dict[str, JsonValue]:
    if platform not in _FORMATS: return {}
    return {'image_formats': _FORMATS[platform][0].split(), 'video_formats': _FORMATS[platform][1].split(),
        'max_image_bytes': _IMAGE_SIZE[platform] * _MIB if platform in _IMAGE_SIZE else None,
        'max_video_bytes': _VIDEO_SIZE[platform] * _MIB if platform in _VIDEO_SIZE else None,
        'max_video_duration_sec': _VIDEO_DURATION.get(platform),
        **({'subtypes': {'REELS': {'max_video_bytes': 300 * _MIB, 'min_duration_sec': 3, 'max_duration_sec': 900},
            'STORIES': {'max_video_bytes': 100 * _MIB, 'min_duration_sec': 3, 'max_duration_sec': 60},
            'IMAGE': {'min_aspect_ratio': 0.8, 'max_aspect_ratio': 1.91}}} if platform == 'instagram' else {})}


def validate_metadata(platform: str, kind: str, metadata: MediaMetadata, options: Mapping[str, JsonValue]) -> None:
    if platform not in _FORMATS or kind not in {'image', 'video'}: raise ChannelError(platform, 'media_kind_unsupported')
    formats = _FORMATS[platform][kind == 'video'].split()
    if metadata.format and metadata.format.lower().removeprefix('.') not in formats:
        raise ChannelError(platform, 'media_format_unsupported')
    maximum = (_VIDEO_SIZE if kind == 'video' else _IMAGE_SIZE).get(platform)
    minimum_duration, maximum_duration = 0, _VIDEO_DURATION.get(platform)
    if platform == 'instagram' and kind == 'video':
        subtype = options.get('media_type', 'REELS')
        if subtype in {'REELS', 'STORIES'}:
            maximum, minimum_duration = (300 if subtype == 'REELS' else 100), 3
            maximum_duration = 900 if subtype == 'REELS' else 60
    if maximum is not None and metadata.size_bytes is not None and metadata.size_bytes > maximum * _MIB:
        raise ChannelError(platform, 'media_size_exceeded')
    if kind == 'video' and metadata.duration_sec is not None:
        if metadata.duration_sec < minimum_duration or maximum_duration is not None and metadata.duration_sec > maximum_duration:
            raise ChannelError(platform, 'media_duration_invalid')
    if platform == 'instagram' and kind == 'image' and options.get('media_type') != 'STORIES' and metadata.width and metadata.height:
        if not 0.8 <= metadata.width / metadata.height <= 1.91: raise ChannelError(platform, 'media_aspect_ratio_invalid')


def validate_local_media(platform: str, path: Path, kind: str, options: Mapping[str, JsonValue]) -> MediaMetadata:
    if not path.is_file() or path.is_symlink(): raise ChannelError(platform, 'media_path_invalid')
    metadata = inspect_local_media(path)
    validate_metadata(platform, kind, metadata, options)
    return metadata


def validate_local_cover(platform: str, path: Path) -> MediaMetadata:
    if platform == 'youtube':
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in {'.jpg', '.jpeg', '.png'} or not 0 < path.stat().st_size <= 50 * _MIB:
            raise ChannelError(platform, 'cover_file_invalid')
        return inspect_local_media(path)
    if platform not in {'instagram', 'pinterest', 'bilibili-open', 'kwai-open', 'wechat-official-open'}:
        raise ChannelError(platform, 'cover_unsupported', status=405)
    return validate_local_media(platform, path, 'image', {'media_type': 'STORIES'} if platform == 'instagram' else {})
