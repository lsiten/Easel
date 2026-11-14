"""Native provider option schemas reimplemented from registered source providers."""
from collections.abc import Mapping
import math
from urllib.parse import urlsplit

from pydantic import JsonValue

from easel.channels.models import ChannelError, JsonDocument
from easel.channels.native_validation import native_text_length

NATIVE_FIELDS = {
    'bilibili-open': {'tid', 'copyright', 'no_reprint', 'source', 'topic_id', 'mission_id', 'content_length'},
    'kwai-open': {'cover', 'stereo_type', 'merchant_product_id', 'content_length'},
    'douyin-open': {'short_title', 'cover_tsp', 'download_type', 'private_status'},
    'wechat-official-open': {'author', 'digest', 'open_comment', 'only_fans_can_comment', 'showCoverPic', 'sourceUrl'},
    'wechat-channels-open': {'workId', 'workLink', 'linkStatus', 'linkMeta'},
    'rednote-link': {'workLink'},
}


def native_options(platform: str, values: Mapping[str, JsonValue]) -> JsonDocument:
    options = dict(values)
    if set(options) - NATIVE_FIELDS[platform]: raise ChannelError(platform, 'publish_option_unsupported')
    for key in ('tid', 'topic_id', 'mission_id', 'content_length', 'cover_tsp'):
        if key in options and (type(options[key]) is not int or options[key] < (0 if key == 'cover_tsp' else 1)):
            raise ChannelError(platform, 'publish_option_number_invalid')
    for key, allowed in {'copyright': {1, 2}, 'no_reprint': {0, 1}, 'download_type': {1, 2}, 'private_status': {0, 1, 2}, 'open_comment': {0, 1}, 'only_fans_can_comment': {0, 1}}.items():
        if key in options and (type(options[key]) is not int or options[key] not in allowed): raise ChannelError(platform, 'publish_option_value_invalid')
    for key, maximum in {'short_title': 12, 'author': 8, 'digest': 120}.items():
        if key in options and (not isinstance(options[key], str) or native_text_length(options[key]) > maximum): raise ChannelError(platform, 'publish_option_length_invalid')
    for key in ('source', 'cover', 'stereo_type', 'merchant_product_id', 'workId', 'workLink'):
        if key in options and not isinstance(options[key], str): raise ChannelError(platform, 'publish_option_type_invalid')
    if options.get('copyright') == 2 and not options.get('source'): raise ChannelError(platform, 'reprint_source_required')
    if 'showCoverPic' in options and type(options['showCoverPic']) is not bool: raise ChannelError(platform, 'publish_option_type_invalid')
    if 'sourceUrl' in options:
        parsed = urlsplit(str(options['sourceUrl']))
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password: raise ChannelError(platform, 'source_url_invalid')
    if 'linkStatus' in options and options['linkStatus'] not in {'pending', 'ready', 'failed'}: raise ChannelError(platform, 'link_status_invalid')
    if 'linkMeta' in options and not isinstance(options['linkMeta'], dict): raise ChannelError(platform, 'link_metadata_invalid')
    if isinstance(options.get('linkMeta'), dict):
        meta = options['linkMeta']
        if set(meta) - {'mediaMd5sum', 'videoClipTaskId', 'scheduledTime'} or any(key in meta and not isinstance(meta[key], str) for key in ('mediaMd5sum', 'videoClipTaskId')):
            raise ChannelError(platform, 'link_metadata_invalid')
        if 'scheduledTime' in meta and (type(meta['scheduledTime']) not in (int, float) or not math.isfinite(meta['scheduledTime'])): raise ChannelError(platform, 'link_metadata_invalid')
    return options
