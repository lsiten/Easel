"""Native constraints run before remote upload through the shared content gate."""
from easel.channels.models import ChannelError, PublishContent
from easel.native_topics import parse_topics


def native_text_length(value: str) -> int:
    return len(value.encode('utf-16-le')) // 2


def validate_native_content(platform: str, content: PublishContent) -> None:
    title_maximum = {'bilibili-open': 79, 'kwai-open': 30, 'douyin-open': 30, 'wechat-official-open': 64,
        'wechat-channels-open': 16, 'rednote-link': 20}[platform]
    if native_text_length(content.title) > title_maximum: raise ChannelError(platform, 'title_too_long')
    if native_text_length(content.body) > {'bilibili-open': 249, 'wechat-official-open': 20000}.get(platform, 1000):
        raise ChannelError(platform, 'body_too_long')
    if platform in {'kwai-open', 'douyin-open'} and native_text_length(content.title) + native_text_length(content.body) > 1000:
        raise ChannelError(platform, 'combined_text_too_long')
    topics = parse_topics(content.body)
    if platform == 'bilibili-open':
        if type(content.options.get('tid')) is not int or content.options['tid'] <= 0: raise ChannelError(platform, 'archive_type_required')
        if not topics or native_text_length(','.join(topics)) > 199: raise ChannelError(platform, 'archive_tags_invalid')
    topic_limit = {'kwai-open': 4, 'douyin-open': 5, 'rednote-link': 5}.get(platform)
    if topic_limit and len(topics) > topic_limit: raise ChannelError(platform, 'topics_too_many')
    if platform == 'rednote-link' and not str(content.options.get('workLink') or '').strip(): raise ChannelError(platform, 'work_link_required')
    if platform == 'wechat-channels-open' and not (content.options.get('workId') or content.options.get('workLink')):
        raise ChannelError(platform, 'work_identity_required')
