"""YouTube category, metadata, captions, and playlist service contracts."""
from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Iterator

from easel.channels.models import ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.options import provider_options
from easel.channels.reads import ChannelReads, _document, _rows


def assert_category(reader: ChannelReads, credential: Credential, category_id: object) -> None:
    if not category_id: return
    if not isinstance(category_id, str): raise ChannelError('youtube', 'category_id_invalid')
    result = reader.call('GET', '/videoCategories', credential, params={'part': 'snippet', 'id': category_id}).data
    if not any(str(row.get('id')) == category_id and _document(row.get('snippet')).get('assignable') is True for row in _rows(result.get('items'))):
        raise ChannelError('youtube', 'category_not_assignable')


def video_metadata(reader: ChannelReads, credential: Credential, content: PublishContent, *, previous: JsonDocument | None = None) -> JsonDocument:
    options = provider_options('youtube', content.options)
    assert_category(reader, credential, options.get('categoryId'))
    tags = list(dict.fromkeys(re.findall(r'(?<!\w)#([\w\u4e00-\u9fff]+)', content.body)))
    if sum(len(tag) for tag in tags) > 500: raise ChannelError('youtube', 'topic_total_length_exceeded')
    description = re.sub(r'(?<!\w)#[\w\u4e00-\u9fff]+', '', content.body).strip()
    previous = previous or {}
    snippet = {key: value for key, value in _document(previous.get('snippet')).items() if key in {'categoryId', 'defaultLanguage', 'defaultAudioLanguage'}}
    snippet.update(title=content.title, description=description, tags=tags)
    if 'categoryId' in options: snippet['categoryId'] = options['categoryId']
    status = {key: value for key, value in _document(previous.get('status')).items()
              if key in {'privacyStatus', 'license', 'embeddable', 'selfDeclaredMadeForKids', 'containsSyntheticMedia', 'publishAt'}}
    status.update({key: options[key] for key in ('privacyStatus', 'license', 'embeddable', 'selfDeclaredMadeForKids', 'containsSyntheticMedia', 'publishAt') if key in options})
    return {'snippet': snippet, 'status': status}


def list_captions(reader: ChannelReads, credential: Credential, work_id: str) -> JsonDocument:
    reader.work(credential, work_id)
    return reader.call('GET', '/captions', credential, params={'part': 'snippet', 'videoId': work_id}).data


def list_playlists(reader: ChannelReads, credential: Credential, cursor: str = '') -> JsonDocument:
    return reader.call('GET', '/playlists', credential, params={'part': 'snippet,contentDetails', 'mine': 'true', 'maxResults': '50',
        **({'pageToken': cursor} if cursor else {})}).data


def add_to_playlist(reader: ChannelReads, credential: Credential, playlist_id: str, work_id: str) -> JsonDocument:
    reader.work(credential, work_id)
    cursor, seen = '', set()
    while True:
        result = list_playlists(reader, credential, cursor)
        if any(str(row.get('id')) == playlist_id for row in _rows(result.get('items'))):
            return reader.call('POST', '/playlistItems', credential, params={'part': 'snippet'}, body={'snippet': {
                'playlistId': playlist_id, 'resourceId': {'kind': 'youtube#video', 'videoId': work_id}}}).data
        after = result.get('nextPageToken')
        if not isinstance(after, str) or not after or after in seen: break
        cursor = after
        seen.add(after)
    raise ChannelError('youtube', 'playlist_owner_unverified', status=403)


def apply_thumbnail(reader: ChannelReads, credential: Credential, receipt: PublishReceipt, path: Path) -> PublishReceipt:
    if not receipt.platform_work_id or receipt.state not in {'published', 'pending'}:
        raise ChannelError('youtube', 'thumbnail_work_identity_required')
    reader.work(credential, receipt.platform_work_id)
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= 50 * 1024**2:
        raise ChannelError('youtube', 'thumbnail_file_invalid')
    mime = 'image/png' if path.suffix.lower() == '.png' else 'image/jpeg' if path.suffix.lower() in {'.jpg', '.jpeg'} else 'application/octet-stream'
    def chunks() -> Iterator[bytes]:
        with path.open('rb') as stream:
            while content := stream.read(1024 * 1024): yield content
    result = reader.http.send('POST', 'https://www.googleapis.com/upload/youtube/v3/thumbnails/set',
        token=credential.access_token.get_secret_value(), params={'videoId': receipt.platform_work_id},
        headers={'Content-Type': mime, 'Content-Length': str(path.stat().st_size)}, content=chunks()).data
    if not _rows(result.get('items')): raise ChannelError('youtube', 'thumbnail_confirmation_missing', status=502)
    return receipt.model_copy(update={'details': {**receipt.details, 'cover_applied': True, 'cover_status': 'applied'}})
