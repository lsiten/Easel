"""Source constraints for reviewed native-app share templates."""
from pathlib import Path

from easel.channels import ChannelError
from easel.channels.native_options import native_options
from easel.channels.rules import validate_local_media
from easel.native_handoff_models import HandoffPlatform, HandoffTemplate
from easel.native_topics import parse_topics
from easel.ownership import MediaManifestEntry, owned_media_path


def validate_template(root: Path, platform: HandoffPlatform, content: HandoffTemplate,
                      manifest: tuple[MediaManifestEntry, ...]) -> None:
    native_options(platform, content.options)
    title_limit, image_limit = (30, 12) if platform == 'douyin-open' else (20, 9)
    length = lambda value: len(value.encode('utf-16-le')) // 2
    if length(content.title) > title_limit or length(content.body) > 1000:
        raise ChannelError(platform, 'text_too_long')
    if platform == 'douyin-open' and length(content.title) + length(content.body) > 1000:
        raise ChannelError(platform, 'combined_text_too_long')
    if len(parse_topics(content.body)) > 5:
        raise ChannelError(platform, 'topics_too_many')
    if len(manifest) != len(content.media):
        raise ChannelError(platform, 'duplicate_media')
    videos = sum(item.kind == 'video' for item in content.media)
    if videos and (videos != 1 or len(content.media) != 1) or not videos and len(content.media) > image_limit:
        raise ChannelError(platform, 'handoff_media_combination_invalid')
    for media in content.media:
        _, path = owned_media_path(root, content.persona, media.path)
        validate_local_media(platform, path, media.kind, content.options)
