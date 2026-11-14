"""Explicit provider work edits, deletion, comments, and publish options."""
from __future__ import annotations

from urllib.parse import quote

from easel.channels.models import ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.reads import ChannelReads, _document, _rows
from easel.channels.options import provider_options
from easel.channels.youtube import video_metadata
from easel.channels.pagination import validate_pagination


def publish_options(reader: ChannelReads, credential: Credential) -> JsonDocument:
    uid = quote(credential.account_uid, safe='')
    match reader.spec.platform:
        case 'tiktok':
            return reader.call('POST', '/post/publish/creator_info/query/', credential, body={}).data
        case 'pinterest':
            boards, cursor, seen = [], '', set()
            while True:
                result = reader.call('GET', '/boards', credential, params={'page_size': '100', **({'bookmark': cursor} if cursor else {})}).data
                boards.extend(_rows(result.get('items')))
                cursor = result.get('bookmark')
                if not isinstance(cursor, str) or not cursor: return {'items': boards}
                if cursor in seen: raise ChannelError('pinterest', 'board_pagination_cycle', status=502)
                seen.add(cursor)
        case 'youtube':
            return reader.call('GET', '/videoCategories', credential, params={'part': 'snippet', 'regionCode': 'US'}).data
        case 'instagram':
            return reader.call('GET', f'/{uid}/content_publishing_limit', credential, params={'fields': 'config,quota_usage'}).data
        case _:
            return {'modes': list(reader.spec.publish_modes)}


def update_work(reader: ChannelReads, credential: Credential, work_id: str, content: PublishContent, *, expected_etag: str = '') -> JsonDocument:
    reader.work(credential, work_id)
    match reader.spec.platform:
        case 'youtube':
            previous = reader.work(credential, work_id)
            if expected_etag and previous.get('etag') != expected_etag: raise ChannelError('youtube', 'work_revision_changed', status=409)
            metadata = video_metadata(reader, credential, content, previous=previous)
            tag = expected_etag if expected_etag.startswith(('"', 'W/"')) else '"' + expected_etag + '"'
            return reader.call('PUT', '/videos', credential, params={'part': 'snippet,status'}, body={'id': work_id, **metadata},
                               headers={'If-Match': tag} if expected_etag else {}).data
        case 'pinterest':
            if expected_etag: raise ChannelError('pinterest', 'conditional_update_unsupported', status=405)
            options = provider_options('pinterest', content.options)
            return reader.call('PATCH', '/pins/' + quote(work_id, safe=''), credential,
                body={'title': content.title, 'description': content.body,
                    **({'link': options['link']} if 'link' in options else {}),
                    **({'board_id': options['boardId']} if 'boardId' in options else {})}).data
        case _:
            raise ChannelError(reader.spec.platform, 'update_unsupported', status=405)


def delete_work(reader: ChannelReads, credential: Credential, work_id: str) -> None:
    reader.work(credential, work_id)
    work = quote(work_id, safe='')
    match reader.spec.platform:
        case 'youtube':
            reader.call('DELETE', '/videos', credential, params={'id': work_id})
        case 'twitter':
            reader.call('DELETE', '/tweets/' + work, credential)
        case 'pinterest':
            reader.call('DELETE', '/pins/' + work, credential)
        case 'instagram' | 'facebook' | 'threads':
            reader.call('DELETE', '/' + work, credential)
        case _:
            raise ChannelError(reader.spec.platform, 'delete_unsupported', status=405)


def comments(reader: ChannelReads, credential: Credential, work_id: str, cursor: str = '', *, limit: int = 25, direction: str = 'next') -> JsonDocument:
    if not reader.spec.engagement:
        raise ChannelError(reader.spec.platform, 'engagement_unsupported', status=405)
    work = quote(work_id, safe='')
    limit = validate_pagination(reader.spec.platform, cursor, limit, direction)
    graph_cursor = {('before' if direction == 'previous' else 'after'): cursor} if cursor else {}
    match reader.spec.platform:
        case 'youtube':
            return reader.call('GET', '/commentThreads', credential, params={'part': 'snippet,replies', 'videoId': work_id,
                               'maxResults': str(limit), **({'pageToken': cursor} if cursor else {})}).data
        case 'instagram' | 'facebook':
            return reader.call('GET', f'/{work}/comments', credential,
                params={'limit': str(limit), 'fields': 'id,text,username,timestamp,replies{id,text,username,timestamp}' if reader.spec.platform == 'instagram' else 'id,message,from,created_time,like_count,comment_count,parent', **graph_cursor}).data
        case 'threads':
            return reader.call('GET', f'/{work}/replies', credential,
                               params={'fields': 'id,text,username,timestamp', 'limit': str(limit), **graph_cursor}).data
        case 'linkedin':
            return reader.call('GET', f'/socialActions/{work}/comments', credential).data
        case 'twitter':
            return reader.call('GET', '/tweets/search/recent', credential,
                params={'query': 'conversation_id:' + work_id, 'tweet.fields': 'author_id,created_at', 'max_results': str(max(10, limit)),
                        **({'next_token': cursor} if cursor else {})}).data
        case _:
            raise ChannelError(reader.spec.platform, 'engagement_unsupported', status=405)


def create_comment(reader: ChannelReads, credential: Credential, work_id: str, text: str) -> JsonDocument:
    if not reader.spec.engagement or not text.strip():
        raise ChannelError(reader.spec.platform, 'comment_unsupported_or_empty')
    work = quote(work_id, safe='')
    match reader.spec.platform:
        case 'youtube':
            return reader.call('POST', '/commentThreads', credential, params={'part': 'snippet'},
                body={'snippet': {'videoId': work_id, 'topLevelComment': {'snippet': {'textOriginal': text}}}}).data
        case 'instagram' | 'facebook':
            return reader.call('POST', f'/{work}/comments', credential, body={'message': text}).data
        case 'threads':
            uid = quote(credential.account_uid, safe='')
            staged = reader.call('POST', f'/{uid}/threads', credential,
                body={'media_type': 'TEXT', 'text': text, 'reply_to_id': work_id}).data
            creation = staged.get('id')
            if not isinstance(creation, str): raise ChannelError('threads', 'comment_identity_missing', status=502)
            return reader.call('POST', f'/{uid}/threads_publish', credential, body={'creation_id': creation}).data
        case 'linkedin':
            actor = credential.account_uid if credential.account_uid.startswith('urn:li:') else f'urn:li:person:{credential.account_uid}'
            return reader.call('POST', f'/socialActions/{work}/comments', credential,
                               body={'actor': actor, 'message': {'text': text}}).data
        case 'twitter':
            return reader.call('POST', '/tweets', credential, body={'text': text, 'reply': {'in_reply_to_tweet_id': work_id}}).data
        case _:
            raise ChannelError(reader.spec.platform, 'engagement_unsupported', status=405)
