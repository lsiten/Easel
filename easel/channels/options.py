"""Provider option names and validation derived from the pinned option schemas."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from urllib.parse import urlsplit
import math
import re

from pydantic import JsonValue

from easel.channels.models import ChannelError, JsonDocument
from easel.channels.native_options import NATIVE_FIELDS, native_options

_FIELDS = {
    'youtube': {'privacyStatus', 'categoryId', 'publishAt', 'license', 'embeddable', 'notifySubscribers', 'selfDeclaredMadeForKids', 'madeForKids', 'containsSyntheticMedia', 'content_length'},
    'tiktok': {'privacy_level', 'disable_duet', 'disable_stitch', 'disable_comment', 'brand_organic_toggle', 'brand_content_toggle', 'auto_add_music', 'photo_cover_index', 'source', 'is_aigc', 'content_length', 'duration_sec'},
    'instagram': {'alt_text', 'caption', 'collaborators', 'cover_url', 'image_url', 'location_id', 'media_type', 'product_tags', 'user_tags'},
    'facebook': {'content_category', 'link', 'content_tags', 'custom_labels', 'direct_share_status', 'embeddable', 'feed_targeting', 'video_state', 'content_length'},
    'threads': {'reply_control', 'location_id', 'allowlisted_country_codes', 'alt_text', 'auto_publish_text', 'link_attachment', 'reply_to_id', 'quote_post_id'},
    'twitter': {'reply_to_tweet_id', 'reply_settings', 'poll', 'quote_tweet_id', 'made_with_ai', 'paid_partnership', 'alt_text'},
    'linkedin': {'visibility', 'authorUrn', 'distribution', 'commentary'},
    'pinterest': {'boardId', 'link', 'altText', 'coverImageUrl'},
    'google-business': set(),
}
_ALIASES = {
    'youtube': {'privacy_status': 'privacyStatus', 'category_id': 'categoryId', 'made_for_kids': 'selfDeclaredMadeForKids'},
    'linkedin': {'author_urn': 'authorUrn'},
    'pinterest': {'board_id': 'boardId', 'alt_text': 'altText', 'cover_image_url': 'coverImageUrl'},
}
_BOOLEANS = {'embeddable', 'notifySubscribers', 'selfDeclaredMadeForKids', 'madeForKids', 'containsSyntheticMedia',
             'disable_duet', 'disable_stitch', 'disable_comment', 'brand_organic_toggle', 'brand_content_toggle',
             'auto_add_music', 'is_aigc', 'auto_publish_text', 'made_with_ai', 'paid_partnership'}


def option_fields(platform: str) -> tuple[str, ...]:
    return tuple(sorted((_FIELDS.get(platform) or NATIVE_FIELDS.get(platform) or set()) - {'content_length', 'duration_sec'}))


def provider_options(platform: str, values: Mapping[str, JsonValue]) -> JsonDocument:
    if platform in NATIVE_FIELDS: return native_options(platform, values)
    aliases = _ALIASES.get(platform, {})
    options = {aliases.get(key, key): value for key, value in values.items()}
    if set(options) - _FIELDS[platform]:
        raise ChannelError(platform, 'publish_option_unsupported')
    for key, value in options.items():
        if key in _BOOLEANS and type(value) is not bool:
            raise ChannelError(platform, 'publish_option_type_invalid')
        if key not in _BOOLEANS | {'poll', 'collaborators', 'product_tags', 'user_tags', 'content_tags', 'custom_labels', 'feed_targeting', 'allowlisted_country_codes', 'direct_share_status', 'photo_cover_index', 'content_length', 'duration_sec'} and not isinstance(value, str):
            raise ChannelError(platform, 'publish_option_type_invalid')
        if key in {'link', 'cover_url', 'image_url', 'link_attachment', 'coverImageUrl'}:
            parsed = urlsplit(value) if isinstance(value, str) else None
            if parsed is None or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ChannelError(platform, 'publish_option_url_invalid')
    enumerations = {
        'privacyStatus': {'public', 'private', 'unlisted'}, 'license': {'youtube', 'creativeCommon'},
        'source': {'FILE_UPLOAD', 'PULL_FROM_URL'}, 'media_type': {'IMAGE', 'REELS', 'STORIES', 'CAROUSEL'},
        'content_category': {'post', 'reel', 'story', 'POST', 'REEL', 'STORY'},
        'visibility': {'PUBLIC', 'CONNECTIONS'}, 'distribution': {'MAIN_FEED', 'NONE'},
        'reply_settings': {'mentionedUsers', 'following', 'subscribers', 'verified'},
        'video_state': {'DRAFT', 'SCHEDULED', 'PUBLISHED'},
        'reply_control': {'everyone', 'accounts_you_follow', 'mentioned_only', 'parent_post_author_only', 'followers_only'},
    }
    for key, choices in enumerations.items():
        if key in options and (not isinstance(options[key], str) or options[key] not in choices):
            raise ChannelError(platform, 'publish_option_value_invalid')
    if 'publishAt' in options:
        try:
            value = datetime.fromisoformat(str(options['publishAt']).replace('Z', '+00:00'))
            if value.tzinfo is None or options.get('privacyStatus', 'public') != 'private': raise ValueError
        except ValueError as error:
            raise ChannelError(platform, 'publish_schedule_invalid') from error
    for key, maximum in {'alt_text': 1000, 'altText': 500, 'caption': 2200, 'commentary': 3000}.items():
        if key in options and (not isinstance(options[key], str) or len(options[key]) > maximum):
            raise ChannelError(platform, 'publish_option_length_invalid')
    for key in ('content_tags', 'custom_labels', 'allowlisted_country_codes'):
        if key in options and (not isinstance(options[key], list) or not all(isinstance(item, str) for item in options[key])):
            raise ChannelError(platform, 'publish_option_list_invalid')
    if 'feed_targeting' in options and not isinstance(options['feed_targeting'], dict): raise ChannelError(platform, 'feed_targeting_invalid')
    for key in ('direct_share_status', 'duration_sec'):
        if key in options and (type(options[key]) not in (int, float) or not math.isfinite(options[key]) or key == 'duration_sec' and options[key] < 0):
            raise ChannelError(platform, 'publish_option_number_invalid')
    for key in ('photo_cover_index', 'content_length'):
        if key in options and (type(options[key]) is not int or options[key] < (1 if key == 'content_length' else 0)):
            raise ChannelError(platform, 'publish_option_number_invalid')
    if 'authorUrn' in options and not re.fullmatch(r'urn:li:person:[^/?#]+', options['authorUrn']):
        raise ChannelError(platform, 'author_urn_invalid')
    for key, identifier in (('product_tags', 'product_id'), ('user_tags', 'username')):
        if key not in options: continue
        if not isinstance(options[key], list): raise ChannelError(platform, 'media_tags_invalid')
        for tag in options[key]:
            if not isinstance(tag, dict) or not isinstance(tag.get(identifier), str) or any(type(tag.get(axis)) not in (int, float) or not math.isfinite(tag[axis]) for axis in ('x', 'y')):
                raise ChannelError(platform, 'media_tags_invalid')
    collaborators = options.get('collaborators')
    if collaborators is not None and (not isinstance(collaborators, list) or len(collaborators) > 3 or not all(isinstance(value, str) for value in collaborators)):
        raise ChannelError(platform, 'collaborators_invalid')
    poll = options.get('poll')
    if poll is not None:
        if not isinstance(poll, dict): raise ChannelError(platform, 'poll_invalid')
        choices, duration = poll.get('options'), poll.get('duration_minutes')
        if not isinstance(choices, list) or not 2 <= len(choices) <= 4 or not all(isinstance(value, str) and 1 <= len(value) <= 25 for value in choices):
            raise ChannelError(platform, 'poll_invalid')
        if type(duration) is not int or not 5 <= duration <= 10080: raise ChannelError(platform, 'poll_invalid')
    return options
