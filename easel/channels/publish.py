"""Official non-Meta publish requests and processing status completion."""
from __future__ import annotations

from urllib.parse import quote, urlsplit
import re
from datetime import datetime, timezone

from easel.channels.models import ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.reads import ChannelReads, _document, _rows
from easel.channels.options import provider_options
from easel.channels.youtube import video_metadata
from easel.channels.rules import validate_metadata
from easel.channels.twitter_text import weighted_length


def validate_content(reader: ChannelReads, content: PublishContent) -> None:
    spec = reader.spec
    options = provider_options(spec.platform, content.options)
    from easel.channels.native_definitions import NATIVE_IDS
    if spec.platform in NATIVE_IDS:
        from easel.channels.native_validation import validate_native_content
        validate_native_content(spec.platform, content)
    if not spec.publish_modes:
        raise ChannelError(spec.platform, 'publish_unsupported', status=405)
    if not content.title.strip() and not content.body.strip() and not content.media:
        raise ChannelError(spec.platform, 'content_empty')
    body_length = weighted_length(content.body or content.title) if spec.platform == 'twitter' else len(content.body or content.title)
    if body_length > spec.max_text:
        raise ChannelError(spec.platform, 'body_too_long')
    title_limit = {'youtube': 100, 'pinterest': 100, 'linkedin': 200, 'bilibili-open': 79, 'kwai-open': 30, 'douyin-open': 30, 'wechat-official-open': 64}.get(spec.platform)
    if title_limit and len(content.title) > title_limit:
        raise ChannelError(spec.platform, 'title_too_long')
    images = sum(media.kind == 'image' for media in content.media)
    videos = sum(media.kind == 'video' for media in content.media)
    if images > spec.max_images or videos > spec.max_videos or (images and videos):
        raise ChannelError(spec.platform, 'media_combination_invalid')
    mode = 'video' if videos else 'image' if images else 'text'
    if mode not in spec.publish_modes:
        raise ChannelError(spec.platform, 'content_mode_unsupported')
    if spec.platform == 'threads' and options.get('auto_publish_text') is True and content.media:
        raise ChannelError(spec.platform, 'auto_publish_text_only')
    if spec.platform == 'instagram':
        media_type = options.get('media_type')
        if (media_type == 'IMAGE' and images != 1) or (media_type == 'REELS' and videos != 1) or (media_type == 'STORIES' and len(content.media) != 1) or (media_type == 'CAROUSEL' and images < 2):
            raise ChannelError(spec.platform, 'media_type_combination_invalid')
        if len(re.findall(r'#\w+', content.body)) > 30 or len(re.findall(r'(^|[^\w.])@[\w.]+', content.body)) > 20:
            raise ChannelError(spec.platform, 'hashtags_or_mentions_exceeded')
    if spec.platform == 'twitter' and options.get('poll') and content.media:
        raise ChannelError(spec.platform, 'poll_media_combination_invalid')
    if spec.platform == 'twitter' and len(content.media) > 1 and any((media.metadata and media.metadata.format.lower().lstrip('.') == 'gif') or urlsplit(media.url).path.lower().endswith('.gif') for media in content.media):
        raise ChannelError(spec.platform, 'animated_gif_combination_invalid')
    if spec.platform == 'tiktok' and len('\n'.join(value.strip() for value in (content.title, content.body) if value.strip())) > 2200:
        raise ChannelError(spec.platform, 'combined_text_too_long')
    for media in content.media:
        if media.metadata is not None: validate_metadata(spec.platform, media.kind, media.metadata, options)
        file_upload = spec.platform in {'bilibili-open', 'kwai-open', 'wechat-official-open'} or (spec.platform == 'tiktok' and content.options.get('source') == 'FILE_UPLOAD') or (spec.platform == 'facebook' and media.kind == 'video' and type(options.get('content_length')) is int)
        if not media.url and not media.media_id and spec.platform != 'youtube' and not file_upload:
            raise ChannelError(spec.platform, 'media_source_required')
        if media.url:
            parsed = urlsplit(media.url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ChannelError(spec.platform, 'media_url_invalid')


def publish_simple(reader: ChannelReads, credential: Credential, content: PublishContent) -> PublishReceipt:
    platform = reader.spec.platform
    options = provider_options(platform, content.options)
    match platform:
        case 'twitter':
            body: JsonDocument = {'text': content.body or content.title}
            body.update({key: options[key] for key in ('reply_settings', 'poll', 'quote_tweet_id', 'made_with_ai', 'paid_partnership') if key in options})
            if 'reply_to_tweet_id' in options: body['reply'] = {'in_reply_to_tweet_id': options['reply_to_tweet_id']}
            if options.get('poll') and content.media: raise ChannelError(platform, 'poll_media_combination_invalid')
            if content.media:
                ids = [media.media_id for media in content.media]
                if not all(ids): raise ChannelError(platform, 'uploaded_media_id_required')
                body['media'] = {'media_ids': ids}
                if options.get('alt_text'):
                    for media in content.media:
                        reader.call('POST', '/media/metadata', credential, body={'id': media.media_id,
                            'metadata': {'alt_text': {'text': options['alt_text']}}})
            result = reader.call('POST', '/tweets', credential, body=body).data
            work_id = str(_document(result.get('data')).get('id') or '')
            if not work_id: raise ChannelError(platform, 'publish_identity_missing', status=502)
            return PublishReceipt(state='published', platform_work_id=work_id, permalink=f'https://x.com/i/status/{work_id}')
        case 'linkedin':
            owner = credential.account_uid if credential.account_uid.startswith('urn:li:') else f'urn:li:person:{credential.account_uid}'
            if options.get('authorUrn', owner) != owner: raise ChannelError(platform, 'author_owner_mismatch', status=403)
            body = {'author': owner, 'commentary': options.get('commentary', content.body or content.title), 'visibility': options.get('visibility', 'PUBLIC'),
                    'distribution': {'feedDistribution': options.get('distribution', 'MAIN_FEED'), 'targetEntities': [], 'thirdPartyDistributionChannels': []},
                    'lifecycleState': 'PUBLISHED', 'isReshareDisabledByAuthor': False}
            if content.media:
                if not all(media.media_id.startswith('urn:li:') for media in content.media):
                    raise ChannelError(platform, 'uploaded_media_urn_required')
                if len(content.media) > 1:
                    body['content'] = {'multiImage': {'images': [{'id': media.media_id} for media in content.media]}}
                else:
                    body['content'] = {'media': {'id': content.media[0].media_id, 'title': content.title}}
            response = reader.call('POST', '/posts', credential, body=body)
            work_id = response.headers.get('x-restli-id') or str(response.data.get('id') or '')
            if not work_id: raise ChannelError(platform, 'publish_identity_missing', status=502)
            return PublishReceipt(state='published', platform_work_id=work_id, permalink=f'https://www.linkedin.com/feed/update/{work_id}')
        case 'pinterest':
            board = options.get('boardId')
            if not isinstance(board, str) or not board: raise ChannelError(platform, 'board_id_required')
            media = content.media[0]
            source = {'source_type': 'image_url', 'url': media.url} if media.kind == 'image' else {'source_type': 'video_id', 'media_id': media.media_id}
            if media.kind == 'video' and options.get('coverImageUrl'): source['cover_image_url'] = options['coverImageUrl']
            if media.kind == 'video' and not media.media_id: raise ChannelError(platform, 'uploaded_media_id_required')
            result = reader.call('POST', '/pins', credential, body={'board_id': board, 'title': content.title,
                                 'description': content.body, 'media_source': source,
                                 **({'link': options['link']} if 'link' in options else {}),
                                 **({'alt_text': options['altText']} if 'altText' in options else {})}).data
            work_id = str(result.get('id') or '')
            if not work_id: raise ChannelError(platform, 'publish_identity_missing', status=502)
            return PublishReceipt(state='published', platform_work_id=work_id, permalink=f'https://www.pinterest.com/pin/{work_id}/')
        case 'tiktok':
            creator = reader.call('POST', '/post/publish/creator_info/query/', credential, body={}).data
            creator_info = _document(creator.get('data'))
            choices = creator_info.get('privacy_level_options', [])
            privacy = options.get('privacy_level')
            if not isinstance(choices, list) or not isinstance(privacy, str) or privacy not in choices:
                raise ChannelError(platform, 'privacy_level_not_allowed')
            post = {'title': content.body or content.title, 'privacy_level': privacy,
                    'disable_comment': options.get('disable_comment', bool(creator_info.get('comment_disabled')))}
            for key, creator_key in {'disable_comment': 'comment_disabled', 'disable_duet': 'duet_disabled', 'disable_stitch': 'stitch_disabled'}.items():
                if creator_info.get(creator_key) is True and options.get(key) is False:
                    raise ChannelError(platform, 'creator_interaction_not_allowed')
                if content.media[0].kind == 'video': post[key] = options.get(key, bool(creator_info.get(creator_key)))
            post.update({key: options[key] for key in ('brand_organic_toggle', 'brand_content_toggle', 'is_aigc', 'auto_add_music') if key in options})
            duration = content.media[0].metadata.duration_sec if content.media[0].metadata else options.get('duration_sec')
            maximum = creator_info.get('max_video_post_duration_sec')
            if type(duration) in (int, float) and type(maximum) in (int, float) and duration > maximum:
                raise ChannelError(platform, 'creator_video_duration_exceeded')
            if content.media[0].kind == 'video':
                post['title'] = '\n'.join(value.strip() for value in (content.title, content.body) if value.strip())
                path = '/post/publish/video/init/'
                source = options.get('source', 'PULL_FROM_URL')
                if source == 'FILE_UPLOAD':
                    size = options.get('content_length')
                    if type(size) is not int or size <= 0: raise ChannelError(platform, 'content_length_required')
                    chunk_size = min(size, 10 * 1024 * 1024)
                    body = {'post_info': post, 'source_info': {'source': source, 'video_size': size,
                        'chunk_size': chunk_size, 'total_chunk_count': max(1, size // chunk_size)}}
                else:
                    if not content.media[0].url: raise ChannelError(platform, 'media_url_required')
                    body = {'post_info': post, 'source_info': {'source': source, 'video_url': content.media[0].url}}
            else:
                if options.get('source') == 'FILE_UPLOAD': raise ChannelError(platform, 'photo_file_upload_unsupported')
                post.update(title=content.title[:90], description=content.body)
                cover = options.get('photo_cover_index', 0)
                if type(cover) is not int or not 0 <= cover < len(content.media):
                    raise ChannelError(platform, 'photo_cover_index_invalid')
                path = '/post/publish/content/init/'
                body = {'post_info': post, 'source_info': {'source': 'PULL_FROM_URL',
                        'photo_cover_index': cover,
                        'photo_images': [media.url for media in content.media]}, 'post_mode': 'DIRECT_POST', 'media_type': 'PHOTO'}
            result = reader.call('POST', path, credential, body=body).data
            publish_id = str(_document(result.get('data')).get('publish_id') or '')
            if not publish_id: raise ChannelError(platform, 'publish_identity_missing', status=502)
            upload_url = str(_document(result.get('data')).get('upload_url') or '')
            details = {'privacy_level': privacy, 'media_kind': content.media[0].kind,
                       'creator_username': str(creator_info.get('creator_username') or '')}
            if upload_url: details['source_info'] = body['source_info']
            return PublishReceipt(state='requires_upload' if upload_url else 'pending', external_request_id=publish_id,
                upload_url=upload_url, details=details)
        case 'youtube':
            size = options.get('content_length')
            if type(size) is not int or size <= 0: raise ChannelError(platform, 'content_length_required')
            privacy = options.get('privacyStatus', 'public')
            if privacy not in {'private', 'public', 'unlisted'}: raise ChannelError(platform, 'privacy_status_invalid')
            metadata = video_metadata(reader, credential, content)
            token = credential.access_token.get_secret_value()
            response = reader.http.send('POST', 'https://www.googleapis.com/upload/youtube/v3/videos', token=token,
                params={'uploadType': 'resumable', 'part': 'snippet,status', 'notifySubscribers': str(options.get('notifySubscribers', False)).lower()},
                headers={'X-Upload-Content-Type': 'video/mp4', 'X-Upload-Content-Length': str(size)},
                body={'snippet': metadata['snippet'], 'status': {'privacyStatus': privacy,
                      'selfDeclaredMadeForKids': options.get('selfDeclaredMadeForKids', False), **_document(metadata['status'])}})
            location = response.headers.get('location')
            if not location: raise ChannelError(platform, 'upload_location_missing', status=502)
            return PublishReceipt(state='requires_upload', external_request_id=location, upload_url=location,
                details={'privacy_status': privacy, 'publish_at': options.get('publishAt', '')})
        case _:
            raise ChannelError(platform, 'publish_unsupported', status=405)


def finalize_tiktok(reader: ChannelReads, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
    result = reader.call('POST', '/post/publish/status/fetch/', credential,
                         body={'publish_id': receipt.external_request_id}).data
    data = _document(result.get('data'))
    status = str(data.get('status') or '')
    if status == 'FAILED': return receipt.model_copy(update={'state': 'failed', 'details': {**receipt.details, 'status': status}})
    ids = data.get('publicaly_available_post_id') or data.get('publicly_available_post_id') or []
    if status == 'PUBLISH_COMPLETE' and not ids and receipt.details.get('privacy_level') == 'SELF_ONLY':
        return receipt.model_copy(update={'state': 'published', 'platform_work_id': '', 'permalink': '',
            'details': {**receipt.details, 'visibility': 'private', 'work_link_status': 'pending', 'provider_status': status}})
    if status != 'PUBLISH_COMPLETE' or not isinstance(ids, list) or len(ids) != 1:
        return receipt.model_copy(update={'state': 'pending', 'details': {**receipt.details, 'status': status}})
    work_id = str(ids[0])
    username = receipt.details.get('creator_username')
    if receipt.details.get('media_kind') == 'image' and isinstance(username, str) and re.fullmatch(r'[A-Za-z0-9_.]+', username):
        return PublishReceipt(state='published', external_request_id=receipt.external_request_id,
            platform_work_id=work_id, permalink=f'https://www.tiktok.com/@{username}/photo/{work_id}',
            details={**receipt.details, 'provider_status': status})
    readback = reader.work(credential, work_id)
    videos = _rows(_document(readback.get('data')).get('videos'))
    if len(videos) != 1 or str(videos[0].get('id')) != work_id:
        raise ChannelError('tiktok', 'work_readback_ambiguous', status=409)
    return PublishReceipt(state='published', external_request_id=receipt.external_request_id,
                          platform_work_id=work_id, permalink=str(videos[0].get('share_url') or ''))


def finalize_youtube(reader: ChannelReads, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
    if not receipt.platform_work_id: raise ChannelError('youtube', 'work_identity_required')
    work = reader.work(credential, receipt.platform_work_id)
    status = _document(work.get('status'))
    if status.get('uploadStatus') in {'failed', 'rejected', 'deleted'}:
        return receipt.model_copy(update={'state': 'failed'})
    scheduled = status.get('publishAt') or receipt.details.get('publish_at')
    if scheduled:
        try:
            instant = datetime.fromisoformat(str(scheduled).replace('Z', '+00:00'))
            if instant.tzinfo is None: raise ValueError
        except ValueError as error:
            raise ChannelError('youtube', 'schedule_response_invalid', status=502) from error
        if instant > datetime.now(timezone.utc) or status.get('privacyStatus') not in {'public', 'unlisted'}:
            return receipt.model_copy(update={'state': 'pending'})
    return receipt.model_copy(update={'state': 'published', 'permalink': f'https://www.youtube.com/watch?v={receipt.platform_work_id}'})
