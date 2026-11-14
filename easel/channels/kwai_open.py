"""Kuaishou Open API credentials, chunk upload, and authoritative video status."""
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlencode, urlsplit
import json

import httpx

from easel.channels.facebook_upload import FileRange
from easel.channels.http import ProviderHttp
from easel.channels.models import AccountProfile, ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.native_config import native_application
from easel.channels.reads import ChannelReads, _document, _rows
from easel.channels.rules import validate_local_cover, validate_local_media


class KwaiOpen:
    def __init__(self, settings: Mapping[str, str], *, transport: httpx.BaseTransport | None = None) -> None:
        self.application = native_application(settings, 'kwai-open')
        self.http = ProviderHttp('kwai-open', transport=transport)

    def call(self, credential: Credential, method: str, path: str, *, params=None, data=None, files=None) -> JsonDocument:
        if credential.platform != 'kwai-open': raise ChannelError('kwai-open', 'credential_invalid', status=403)
        result = self.http.send(method, 'https://open.kuaishou.com' + path,
            params={'app_id': self.application.client_id, 'access_token': credential.access_token.get_secret_value(), **(params or {})},
            data=data, files=files).data
        if result.get('result') not in (1, '1'): raise ChannelError('kwai-open', 'provider_api_error', status=502)
        return result

    def profile(self, credential: Credential) -> AccountProfile:
        if credential.account_uid == 'pending': raise ChannelError('kwai-open', 'profile_identity_missing', status=502)
        value = _document(self.call(credential, 'GET', '/openapi/user_info').get('user_info'))
        if not value: raise ChannelError('kwai-open', 'profile_missing', status=502)
        return AccountProfile(account_uid=credential.account_uid, display_name=str(value.get('name') or ''),
            avatar_url=str(value.get('bigHead') or value.get('head') or ''), fans_count=ChannelReads._count(value.get('fan')),
            following_count=ChannelReads._count(value.get('follow')), extra={'client_id': self.application.client_id})

    def works(self, credential: Credential, cursor: str = '', limit: int = 20) -> JsonDocument:
        if not 1 <= limit <= 200: raise ChannelError('kwai-open', 'limit_invalid')
        value = self.call(credential, 'GET', '/openapi/photo/list', params={'cursor': cursor, 'count': str(limit)})
        rows = _rows(value.get('video_list'))
        dated = [row for row in rows if row.get('photo_id') and str(row.get('create_time') or '').isdecimal()]
        oldest = min(dated, key=lambda row: int(row['create_time'])) if dated else {}
        return {**value, 'items': rows, 'next_cursor': str(oldest.get('photo_id') or ''), 'has_next': len(rows) >= limit}

    def work(self, credential: Credential, work_id: str) -> JsonDocument:
        value = _document(self.call(credential, 'GET', '/openapi/photo/info', params={'photo_id': work_id}).get('video_info'))
        if str(value.get('photo_id') or '') != work_id: raise ChannelError('kwai-open', 'work_identity_mismatch', status=403)
        return {**value, 'id': work_id, 'title': value.get('caption'), 'description': value.get('caption'),
            'author_id': credential.account_uid, 'permalink': 'https://www.kuaishou.com/short-video/' + work_id}

    def analytics(self, credential: Credential, work_id: str = '') -> JsonDocument:
        return self.work(credential, work_id) if work_id else self.profile(credential).model_dump(mode='json')

    def initialize_upload(self, credential: Credential, path: Path) -> PublishReceipt:
        validate_local_media('kwai-open', path, 'video', {})
        value = self.call(credential, 'POST', '/openapi/photo/start_upload')
        endpoint = str(value.get('endpoint') or '')
        token = str(value.get('upload_token') or '')
        parsed = urlsplit('http://' + endpoint)
        if not token or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ChannelError('kwai-open', 'upload_endpoint_invalid', status=502)
        return PublishReceipt(state='requires_upload', external_request_id=token, upload_url='http://' + endpoint.rstrip('/'),
            details={'stage': 'media_upload', 'size': path.stat().st_size})

    def _fragment(self, receipt: PublishReceipt, edge: str, params: dict[str, str], stream=None, length: int = 0) -> None:
        url = receipt.upload_url + '/api/upload/' + edge + '?' + urlencode({'upload_token': receipt.external_request_id, **params})
        headers = {'Content-Type': 'video/mp4', 'Content-Length': str(length)}
        try:
            if self.http.transport is not None:
                with httpx.Client(transport=self.http.transport, timeout=60, trust_env=False, follow_redirects=False) as client:
                    response = client.post(url, headers=headers, content=iter(lambda: stream.read(1024**2), b'') if stream else b'')
                    raw, status = response.content, response.status_code
            else:
                from easel.remote_media import public_opener
                from urllib.request import Request, HTTPRedirectHandler
                class NoRedirect(HTTPRedirectHandler):
                    def redirect_request(self, req, fp, code, msg, headers, newurl):
                        raise ChannelError('kwai-open', 'upload_redirect_rejected', status=502)
                with public_opener(NoRedirect()).open(Request(url, data=stream if stream else b'', headers=headers, method='POST'), timeout=60) as response:
                    raw, status = response.read(1024**2), response.status
            value = json.loads(raw)
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise ChannelError('kwai-open', 'upload_transport_error', status=502) from error
        if not 200 <= status < 300 or value.get('result') not in (1, '1'): raise ChannelError('kwai-open', 'upload_rejected', status=502)

    def upload(self, credential: Credential, receipt: PublishReceipt, path: Path) -> PublishReceipt:
        if receipt.state != 'requires_upload' or receipt.details.get('size') != path.stat().st_size: raise ChannelError('kwai-open', 'upload_state_invalid')
        chunk, size = 4 * 1024**2, path.stat().st_size
        count = 0
        for first in range(0, size, chunk):
            length = min(chunk, size - first)
            with FileRange(path, first, length) as stream:
                self._fragment(receipt, 'fragment', {'fragment_id': str(count)}, stream, length)
            count += 1
        self._fragment(receipt, 'complete', {'fragment_count': str(count)})
        return receipt.model_copy(update={'state': 'requires_finalize', 'details': {**receipt.details, 'stage': 'media_ready', 'media_ready': True}})

    def publish(self, credential: Credential, content: PublishContent, cover_path: Path | None = None) -> PublishReceipt:
        token = content.media[0].media_id if content.media else ''
        if not token: raise ChannelError('kwai-open', 'uploaded_media_id_required')
        if not cover_path: raise ChannelError('kwai-open', 'cover_required')
        validate_local_cover('kwai-open', cover_path)
        with cover_path.open('rb') as stream:
            value = self.call(credential, 'POST', '/openapi/photo/publish', params={'upload_token': token,
                **{key: str(content.options[key]) for key in ('stereo_type', 'merchant_product_id') if key in content.options}},
                data={'caption': content.body.strip()}, files={'cover': (cover_path.name, stream, 'image/jpeg')})
        identity = str(_document(value.get('video_info')).get('photo_id') or '')
        if not identity: raise ChannelError('kwai-open', 'publication_identity_missing', status=502)
        return PublishReceipt(state='pending', external_request_id=identity)

    def finalize(self, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
        if receipt.details.get('stage') == 'media_ready': return receipt
        value = self.work(credential, receipt.external_request_id)
        if value.get('pending') is not False: return receipt.model_copy(update={'state': 'pending'})
        return receipt.model_copy(update={'state': 'published', 'platform_work_id': receipt.external_request_id,
            'permalink': 'https://www.kuaishou.com/short-video/' + receipt.external_request_id})
