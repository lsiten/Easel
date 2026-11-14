"""Native Page video chunks and authoritative processing completion."""
from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import quote

from easel.channels.models import ChannelError, Credential, JsonDocument, PublishReceipt
from easel.channels.reads import ChannelReads, _document


class FileRange(io.IOBase):
    def __init__(self, path: Path, first: int, length: int) -> None:
        self.stream, self.first, self.length, self.position = path.open('rb'), first, length, 0

    def read(self, size: int = -1) -> bytes:
        self.stream.seek(self.first + self.position)
        data = self.stream.read(min(size, self.length - self.position) if size >= 0 else self.length - self.position)
        self.position += len(data)
        return data

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = 0) -> int:
        position = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        if not 0 <= position <= self.length: raise ValueError('media range seek invalid')
        self.position = position
        return position

    def close(self) -> None:
        self.stream.close()
        super().close()


def start_feed_video(reader: ChannelReads, credential: Credential, options: JsonDocument, title: str, description: str) -> PublishReceipt:
    size = options.get('content_length')
    if type(size) is not int or size <= 0: raise ChannelError('facebook', 'content_length_required')
    result = reader.call('POST', '/' + quote(credential.account_uid, safe='') + '/videos', credential,
        params={'upload_phase': 'start', 'file_size': str(size)}).data
    session = str(result.get('upload_session_id') or '')
    if not session: raise ChannelError('facebook', 'upload_identity_missing', status=502)
    return PublishReceipt(state='requires_upload', external_request_id=session, details={
        'edge': 'videos', 'file_size': size, 'video_id': result.get('video_id'),
        'start_offset': result.get('start_offset', 0), 'end_offset': result.get('end_offset', size),
        'finish_options': {key: value for key, value in options.items() if key != 'content_length'},
        'title': title, 'description': description})


def upload_feed_video(reader: ChannelReads, credential: Credential, receipt: PublishReceipt, path: Path) -> PublishReceipt:
    size = path.stat().st_size
    if receipt.details.get('file_size') != size: raise ChannelError('facebook', 'upload_size_mismatch')
    try:
        first, last = int(receipt.details.get('start_offset', 0)), int(receipt.details.get('end_offset', size))
    except (ValueError, TypeError) as error:
        raise ChannelError('facebook', 'upload_range_invalid') from error
    endpoint = '/' + quote(credential.account_uid, safe='') + '/videos'
    while first < size:
        if not first < last <= size: raise ChannelError('facebook', 'upload_range_invalid')
        with FileRange(path, first, last - first) as stream:
            result = reader.http.send('POST', reader.spec.api_base + endpoint,
                token=credential.access_token.get_secret_value(), params={'upload_phase': 'transfer',
                    'upload_session_id': receipt.external_request_id, 'start_offset': str(first)},
                files={'video_file_chunk': (path.name, stream, 'application/octet-stream')}).data
        try:
            after, end = int(result.get('start_offset', size)), int(result.get('end_offset', size))
        except (ValueError, TypeError) as error:
            raise ChannelError('facebook', 'upload_range_invalid') from error
        if after <= first or after > size or end > size: raise ChannelError('facebook', 'upload_range_invalid')
        first, last = after, end
    result = reader.call('POST', endpoint, credential, body={**_document(receipt.details.get('finish_options')),
        'upload_phase': 'finish', 'upload_session_id': receipt.external_request_id,
        'title': receipt.details.get('title', ''), 'description': receipt.details.get('description', '')}).data
    identity = str(result.get('id') or receipt.details.get('video_id') or '')
    if not identity or result.get('success') is False: raise ChannelError('facebook', 'upload_finalize_failed', status=502)
    return receipt.model_copy(update={'state': 'pending', 'platform_work_id': identity,
        'details': {**receipt.details, 'video_id': identity}})


def finalize_facebook(reader: ChannelReads, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
    video_id = str(receipt.details.get('video_id') or receipt.platform_work_id or receipt.external_request_id)
    result = reader.call('GET', '/' + quote(video_id, safe=''), credential, params={'fields': 'id,status'}).data
    if str(result.get('id') or '') != video_id: raise ChannelError('facebook', 'video_identity_mismatch', status=502)
    status = str(_document(result.get('status')).get('video_status') or '').lower()
    if status in {'error', 'failed'}: return receipt.model_copy(update={'state': 'failed'})
    if status not in {'ready', 'published'}: return receipt.model_copy(update={'state': 'pending'})
    work_id = receipt.platform_work_id or video_id
    work = reader.work(credential, work_id)
    author = _document(work.get('from')).get('id')
    if author and str(author) != credential.account_uid: raise ChannelError('facebook', 'work_owner_mismatch', status=403)
    if str(work.get('id') or '') != work_id: raise ChannelError('facebook', 'work_readback_ambiguous', status=502)
    url = str(work.get('permalink_url') or f'https://www.facebook.com/{work_id}')
    return receipt.model_copy(update={'state': 'published', 'platform_work_id': work_id, 'permalink': url})
