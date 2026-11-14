"""Container publishing and completion for Meta platform APIs."""
from __future__ import annotations

from urllib.parse import quote
import re

from easel.channels.models import ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.reads import ChannelReads, _document
from easel.channels.options import provider_options
from easel.channels.facebook_upload import start_feed_video


def _identifier(payload: JsonDocument, platform: str) -> str:
    value = payload.get('id')
    if not isinstance(value, (str, int)) or not str(value):
        raise ChannelError(platform, 'publish_identity_missing', status=502)
    return str(value)


def _discard_children(reader: ChannelReads, credential: Credential, children: list[str]) -> None:
    for child in children:
        try:
            reader.call('DELETE', '/' + quote(child, safe=''), credential)
        except ChannelError:
            # Containers remain unpublished and expire at the provider after failed cleanup.
            continue


def publish_graph(reader: ChannelReads, credential: Credential, content: PublishContent) -> PublishReceipt:
    platform = reader.spec.platform
    options = provider_options(platform, content.options)
    uid = quote(credential.account_uid, safe='')
    text = content.body or content.title
    topic = ''
    if platform == 'threads':
        topics = re.findall(r'(?<!\w)#([\w\u4e00-\u9fff]+)', text)
        topic = topics[0] if topics else ''
        text = re.sub(r'(?<!\w)#[\w\u4e00-\u9fff]+', '', text).strip()
    if platform == 'facebook':
        body: JsonDocument = {'message': text}
        body.update({key: options[key] for key in ('link', 'content_tags', 'custom_labels', 'direct_share_status', 'embeddable', 'feed_targeting') if key in options})
        category = str(options.get('content_category', 'post')).lower()
        if category in {'story', 'reel'}:
            if len(content.media) != 1 or (category == 'reel' and content.media[0].kind != 'video'):
                raise ChannelError(platform, 'media_combination_invalid')
            if category == 'story' and (content.title or content.body or options.get('link')):
                raise ChannelError(platform, 'story_text_not_supported')
            media = content.media[0]
            if media.kind == 'image':
                uploaded = reader.call('POST', f'/{uid}/photos', credential, body={'url': media.url, 'published': False}).data
                result = reader.call('POST', f'/{uid}/photo_stories', credential, body={'photo_id': _identifier(uploaded, platform)}).data
                work_id = str(result.get('post_id') or '')
                if not work_id: raise ChannelError(platform, 'publish_identity_missing', status=502)
                return PublishReceipt(state='published', platform_work_id=work_id, permalink=f'https://www.facebook.com/{work_id}')
            edge = 'video_stories' if category == 'story' else 'video_reels'
            started = reader.call('POST', f'/{uid}/{edge}', credential, params={'upload_phase': 'start'}).data
            video_id, upload_url = str(started.get('video_id') or ''), str(started.get('upload_url') or '')
            if not video_id or not upload_url: raise ChannelError(platform, 'upload_identity_missing', status=502)
            return PublishReceipt(state='requires_upload', external_request_id=video_id, upload_url=upload_url,
                details={'edge': edge, 'description': text, 'title': content.title, 'content_category': category,
                         'video_state': options.get('video_state', 'PUBLISHED')})
        if content.media and content.media[0].kind == 'video':
            return start_feed_video(reader, credential, options, content.title, text)
        elif content.media:
            attachments = []
            for media in content.media:
                uploaded = reader.call('POST', f'/{uid}/photos', credential,
                                       body={'url': media.url, 'published': False}).data
                attachments.append({'media_fbid': _identifier(uploaded, platform)})
            result = reader.call('POST', f'/{uid}/feed', credential, body={**body, 'attached_media': attachments}).data
        else:
            result = reader.call('POST', f'/{uid}/feed', credential, body=body).data
        work_id = _identifier(result, platform)
        return PublishReceipt(state='published', platform_work_id=work_id,
                              permalink=f'https://www.facebook.com/{work_id}')
    edge = 'media' if platform == 'instagram' else 'threads'
    children = []
    if len(content.media) > 1:
        try:
            for media in content.media:
                item = {'video_url' if media.kind == 'video' else 'image_url': media.url, 'is_carousel_item': True}
                if platform == 'threads': item['media_type'] = 'IMAGE'
                if media.kind == 'video': item['media_type'] = 'VIDEO'
                child = reader.call('POST', f'/{uid}/{edge}', credential, body=item).data
                children.append(_identifier(child, platform))
        except ChannelError:
            _discard_children(reader, credential, children)
            raise
    body = {'caption' if platform == 'instagram' else 'text': text}
    if topic: body['topic_tag'] = topic
    body.update({key: value for key, value in options.items() if key not in {'media_type', 'image_url'}})
    if children:
        body.update(media_type='CAROUSEL', children=','.join(children))
    elif content.media:
        media = content.media[0]
        body['video_url' if media.kind == 'video' else 'image_url'] = media.url
        if media.kind == 'video': body['media_type'] = 'REELS' if platform == 'instagram' else 'VIDEO'
        elif platform == 'threads': body['media_type'] = 'IMAGE'
    else:
        body['media_type'] = 'TEXT'
    if platform == 'instagram' and 'media_type' in options:
        media_type = options['media_type']
        if media_type == 'STORIES' and len(content.media) != 1: raise ChannelError(platform, 'story_media_count_invalid')
        if media_type == 'REELS' and (len(content.media) != 1 or content.media[0].kind != 'video'): raise ChannelError(platform, 'reels_video_required')
        body['media_type'] = media_type
    try:
        result = reader.call('POST', f'/{uid}/{edge}', credential, body=body).data
    except ChannelError:
        _discard_children(reader, credential, children)
        raise
    if platform == 'threads' and options.get('auto_publish_text') is True:
        if content.media: raise ChannelError(platform, 'auto_publish_text_only')
        work_id = _identifier(result, platform)
        work = reader.work(credential, work_id)
        return PublishReceipt(state='published', platform_work_id=work_id, permalink=str(work.get('permalink') or ''))
    return PublishReceipt(state='pending', external_request_id=_identifier(result, platform), details={'child_container_ids': children})


def finalize_graph(reader: ChannelReads, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
    platform = reader.spec.platform
    container_id = receipt.external_request_id
    if not container_id:
        raise ChannelError(platform, 'container_id_required')
    fields = 'status_code,status' if platform == 'instagram' else 'id,status'
    status = reader.call('GET', '/' + quote(container_id, safe=''), credential, params={'fields': fields}).data
    value = str(status.get('status_code') or status.get('status') or '')
    if value in {'ERROR', 'EXPIRED', 'FAILED'}:
        return receipt.model_copy(update={'state': 'failed', 'details': {'status': value}})
    if value not in {'FINISHED', 'PUBLISHED'}:
        return receipt.model_copy(update={'state': 'pending', 'details': {'status': value}})
    if value == 'PUBLISHED':
        # The container has completed already. Resolve the existing work through read-back.
        return receipt.model_copy(update={'state': 'pending', 'details': {'status': value, 'requires_readback': True}})
    edge = 'media_publish' if platform == 'instagram' else 'threads_publish'
    result = reader.call('POST', '/' + quote(credential.account_uid, safe='') + '/' + edge,
                         credential, body={'creation_id': container_id}).data
    work_id = _identifier(result, platform)
    work = reader.work(credential, work_id)
    return PublishReceipt(state='published', external_request_id=container_id,
                          platform_work_id=work_id, permalink=str(work.get('permalink') or ''))
