"""Explicit media-upload phases whose IDs can be checkpointed before publishing."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from easel.channels.models import ChannelError, Credential, PublishReceipt
from easel.channels.reads import ChannelReads, _document, _rows
from easel.channels.publish import finalize_youtube
from easel.channels.facebook_upload import upload_feed_video


def _chunks(path: Path, first: int = 0, length: int | None = None) -> Iterator[bytes]:
    with path.open('rb') as stream:
        stream.seek(first)
        remaining = length
        while remaining is None or remaining > 0:
            chunk = stream.read(min(1024 * 1024, remaining) if remaining is not None else 1024 * 1024)
            if not chunk: break
            yield chunk
            if remaining is not None: remaining -= len(chunk)


def initialize_upload(reader: ChannelReads, credential: Credential, path: Path, kind: str) -> PublishReceipt:
    if not path.is_file() or path.is_symlink() or kind not in {'image', 'video'}:
        raise ChannelError(reader.spec.platform, 'media_path_invalid')
    size = path.stat().st_size
    if size <= 0: raise ChannelError(reader.spec.platform, 'media_empty')
    match reader.spec.platform:
        case 'linkedin':
            owner = credential.account_uid if credential.account_uid.startswith('urn:li:') else f'urn:li:person:{credential.account_uid}'
            edge = 'videos' if kind == 'video' else 'images'
            body = {'owner': owner}
            if kind == 'video': body['fileSizeBytes'] = size
            result = reader.call('POST', '/' + edge + '?action=initializeUpload', credential,
                                 body={'initializeUploadRequest': body}).data
            value = _document(result.get('value'))
            media_id = str(value.get('video') or value.get('image') or '')
            if not media_id: raise ChannelError('linkedin', 'upload_identity_missing', status=502)
            return PublishReceipt(state='requires_upload', external_request_id=media_id,
                                  upload_url=str(value.get('uploadUrl') or ''), details=value)
        case 'pinterest':
            if kind != 'video': raise ChannelError('pinterest', 'direct_image_url_required')
            result = reader.call('POST', '/media', credential, body={'media_type': 'video'}).data
            media_id = str(result.get('media_id') or '')
            if not media_id: raise ChannelError('pinterest', 'upload_identity_missing', status=502)
            return PublishReceipt(state='requires_upload', external_request_id=media_id,
                                  upload_url=str(result.get('upload_url') or ''), details=result)
        case 'twitter':
            extension = path.suffix.lower()
            media_type = {'.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp', '.mov': 'video/quicktime'}.get(extension, 'video/mp4' if kind == 'video' else 'image/jpeg')
            category = 'tweet_video' if kind == 'video' else 'tweet_gif' if extension == '.gif' else 'tweet_image'
            result = reader.call('POST', '/media/upload/initialize', credential,
                                 body={'media_type': media_type, 'total_bytes': size,
                                       'media_category': category}).data
            data = _document(result.get('data'))
            media_id = str(data.get('id') or '')
            if not media_id: raise ChannelError('twitter', 'upload_identity_missing', status=502)
            return PublishReceipt(state='requires_upload', external_request_id=media_id,
                details={'chunk_size': 1024 * 1024 if kind == 'video' else size, 'content_length': size})
        case _:
            raise ChannelError(reader.spec.platform, 'media_upload_unsupported', status=405)


def upload_file(reader: ChannelReads, credential: Credential, receipt: PublishReceipt, path: Path) -> PublishReceipt:
    if receipt.state != 'requires_upload' or not path.is_file() or path.is_symlink():
        raise ChannelError(reader.spec.platform, 'upload_state_invalid')
    token = credential.access_token.get_secret_value()
    match reader.spec.platform:
        case 'facebook':
            edge = receipt.details.get('edge')
            if edge == 'videos': return upload_feed_video(reader, credential, receipt, path)
            if edge not in {'video_reels', 'video_stories'}: raise ChannelError('facebook', 'upload_state_invalid')
            reader.http.send('POST', receipt.upload_url, headers={'Authorization': 'OAuth ' + token,
                'offset': '0', 'file_size': str(path.stat().st_size), 'Content-Type': 'application/octet-stream',
                'Content-Length': str(path.stat().st_size)}, content=_chunks(path))
            body = {'upload_phase': 'finish', 'video_id': receipt.external_request_id,
                    'content_category': receipt.details.get('content_category')}
            if edge == 'video_reels': body.update(video_state=receipt.details.get('video_state', 'PUBLISHED'), description=receipt.details.get('description', ''))
            result = reader.call('POST', '/' + quote(credential.account_uid, safe='') + '/' + edge, credential, body=body).data
            confirmed = result.get('success') is True if edge == 'video_reels' else bool(result.get('post_id'))
            if not confirmed: raise ChannelError('facebook', 'upload_finalize_failed', status=502)
            work_id = str(result.get('post_id') or receipt.external_request_id)
            return receipt.model_copy(update={'state': 'pending', 'platform_work_id': work_id, 'upload_url': '',
                'details': {**receipt.details, 'video_id': receipt.external_request_id}})
        case 'tiktok':
            source = _document(receipt.details.get('source_info'))
            size, chunk_size, count = source.get('video_size'), source.get('chunk_size'), source.get('total_chunk_count')
            if type(size) is not int or size != path.stat().st_size or type(chunk_size) is not int or chunk_size <= 0 or type(count) is not int or count <= 0:
                raise ChannelError('tiktok', 'upload_size_mismatch')
            for index in range(count):
                first = index * chunk_size
                length = size - first if index == count - 1 else chunk_size
                reader.http.send('PUT', receipt.upload_url, headers={'Content-Type': 'video/mp4',
                    'Content-Length': str(length), 'Content-Range': f'bytes {first}-{first + length - 1}/{size}'},
                    content=_chunks(path, first, length))
            return receipt.model_copy(update={'state': 'pending'})
        case 'youtube':
            response = reader.http.send('PUT', receipt.upload_url, token=token,
                headers={'Content-Type': 'video/mp4', 'Content-Length': str(path.stat().st_size)}, content=_chunks(path))
            work_id = str(response.data.get('id') or '')
            if not work_id: raise ChannelError('youtube', 'upload_identity_missing', status=502)
            return finalize_youtube(reader, credential, receipt.model_copy(update={
                'state': 'pending', 'platform_work_id': work_id, 'upload_url': ''}))
        case 'linkedin':
            parts = _rows(receipt.details.get('uploadInstructions'))
            if not parts:
                parts = [{'uploadUrl': receipt.upload_url, 'firstByte': 0, 'lastByte': path.stat().st_size - 1}]
            etags = []
            for part in parts:
                first = int(part.get('firstByte') or 0)
                length = int(part.get('lastByte', path.stat().st_size - 1)) - first + 1
                if first < 0 or length <= 0 or first + length > path.stat().st_size:
                    raise ChannelError('linkedin', 'upload_range_invalid')
                response = reader.http.send('PUT', str(part.get('uploadUrl') or ''),
                    headers={'Content-Type': 'application/octet-stream', 'Content-Length': str(length)}, content=_chunks(path, first, length))
                if response.headers.get('etag'): etags.append(response.headers['etag'])
            if receipt.external_request_id.startswith('urn:li:video:'):
                reader.call('POST', '/videos?action=finalizeUpload', credential,
                    body={'finalizeUploadRequest': {'video': receipt.external_request_id,
                          'uploadToken': receipt.details.get('uploadToken') or '', 'uploadedPartIds': etags}})
            return receipt.model_copy(update={'state': 'requires_finalize'})
        case 'pinterest':
            params = _document(receipt.details.get('upload_parameters'))
            with path.open('rb') as stream:
                reader.http.send('POST', receipt.upload_url, data={key: str(value) for key, value in params.items()},
                    files={'file': (path.name, stream, 'video/mp4')}, storage_upload=True)
            return receipt.model_copy(update={'state': 'requires_finalize'})
        case 'twitter':
            chunk_size = receipt.details.get('chunk_size', 5 * 1024 * 1024)
            if type(chunk_size) is not int or not 0 < chunk_size <= 5 * 1024 * 1024: raise ChannelError('twitter', 'upload_chunk_invalid')
            if receipt.details.get('content_length', path.stat().st_size) != path.stat().st_size: raise ChannelError('twitter', 'upload_size_mismatch')
            with path.open('rb') as stream:
                segment = 0
                while chunk := stream.read(chunk_size):
                    reader.http.send('POST', reader.spec.api_base + '/media/upload/' + quote(receipt.external_request_id, safe='') + '/append',
                        token=token, data={'segment_index': str(segment)}, files={'media': ('media', chunk, 'application/octet-stream')})
                    segment += 1
            result = reader.call('POST', '/media/upload/' + quote(receipt.external_request_id, safe='') + '/finalize', credential).data
            return receipt.model_copy(update={'state': 'requires_finalize', 'details': result})
        case _:
            raise ChannelError(reader.spec.platform, 'media_upload_unsupported', status=405)


def media_status(reader: ChannelReads, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
    media_id = quote(receipt.external_request_id, safe='')
    match reader.spec.platform:
        case 'pinterest':
            result = reader.call('GET', f'/media/{media_id}', credential).data
            status = str(result.get('status') or '')
            state = 'failed' if status == 'failed' else 'requires_finalize' if status == 'succeeded' else 'pending'
        case 'twitter':
            result = reader.call('GET', '/media/upload', credential,
                                 params={'media_id': receipt.external_request_id, 'command': 'STATUS'}).data
            data = _document(result.get('data'))
            info = _document(data.get('processing_info') or data.get('processingInfo'))
            status = str(info.get('state') or '')
            state = 'failed' if status == 'failed' else 'requires_finalize' if status in {'', 'succeeded'} else 'pending'
        case 'linkedin':
            edge = 'videos' if receipt.external_request_id.startswith('urn:li:video:') else 'images'
            result = reader.call('GET', f'/{edge}/{media_id}', credential).data
            status = str(result.get('status') or '')
            state = 'failed' if status in {'PROCESSING_FAILED', 'FAILED'} else 'requires_finalize' if status == 'AVAILABLE' else 'pending'
        case _:
            raise ChannelError(reader.spec.platform, 'media_status_unsupported', status=405)
    return receipt.model_copy(update={'state': state, 'details': {**result, 'media_ready': state == 'requires_finalize'}})
