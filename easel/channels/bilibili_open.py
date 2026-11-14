"""Bilibili Open Platform signed requests and checkpointable video submission."""
from collections.abc import Mapping
import hashlib
import hmac
import json
from pathlib import Path
import re
import time
import uuid

import httpx

from easel.channels.facebook_upload import FileRange
from easel.channels.http import ProviderHttp
from easel.channels.models import AccountProfile, ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.native_config import native_application
from easel.channels.reads import ChannelReads, _document, _rows
from easel.channels.rules import validate_local_cover, validate_local_media
from easel.native_topics import parse_topics, strip_topics
from easel.channels.native_validation import native_text_length


class BilibiliOpen:
    def __init__(self, settings: Mapping[str, str], *, transport: httpx.BaseTransport | None = None) -> None:
        self.application = native_application(settings, 'bilibili-open')
        self.http = ProviderHttp('bilibili-open', transport=transport)

    def headers(self, credential: Credential, content: bytes = b'') -> dict[str, str]:
        if credential.platform != 'bilibili-open' or not credential.access_token.get_secret_value(): raise ChannelError('bilibili-open', 'credential_invalid', status=403)
        fields = {'x-bili-accesskeyid': self.application.client_id, 'x-bili-content-md5': hashlib.md5(content).hexdigest(),
            'x-bili-signature-method': 'HMAC-SHA256', 'x-bili-signature-nonce': str(uuid.uuid4()),
            'x-bili-signature-version': '2.0', 'x-bili-timestamp': str(int(time.time()))}
        signature = hmac.new(self.application.client_secret.get_secret_value().encode(),
            '\n'.join(key + ':' + value for key, value in fields.items()).encode(), hashlib.sha256).hexdigest()
        return {**fields, 'Access-Token': credential.access_token.get_secret_value(), 'Authorization': signature, 'Accept': 'application/json'}

    def call(self, credential: Credential, method: str, path: str, *, params=None, body=None, files=None) -> JsonDocument:
        encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode() if body is not None else b''
        headers = self.headers(credential, encoded)
        if files is None: headers['Content-Type'] = 'application/json'
        result = self.http.send(method, 'https://member.bilibili.com' + path, params=params, headers=headers,
            content=encoded if body is not None else None, files=files).data
        if result.get('code') not in (0, '0', None): raise ChannelError('bilibili-open', 'provider_api_error', str(result.get('code')), 502)
        value = result.get('data')
        return value if isinstance(value, dict) else {'items': value} if isinstance(value, list) else {}

    def profile(self, credential: Credential) -> AccountProfile:
        value = self.call(credential, 'GET', '/arcopen/fn/user/account/info')
        uid = str(value.get('openid') or '')
        if not uid: raise ChannelError('bilibili-open', 'profile_identity_missing', status=502)
        if credential.account_uid not in {'pending', uid}: raise ChannelError('bilibili-open', 'account_owner_mismatch', status=403)
        stats = {}
        try:
            stats = self.analytics(credential)
        except ChannelError:
            pass
        return AccountProfile(account_uid=uid, display_name=str(value.get('name') or ''), avatar_url=str(value.get('face') or ''),
            fans_count=ChannelReads._count(stats.get('follower')), following_count=ChannelReads._count(stats.get('following')),
            extra={'client_id': self.application.client_id, 'archive_count': stats.get('arc_passed_total')})

    def analytics(self, credential: Credential, work_id: str = '') -> JsonDocument:
        return self.call(credential, 'GET', '/arcopen/fn/data/arc/stat' if work_id else '/arcopen/fn/data/user/stat',
            params={'resource_id': work_id} if work_id else {})

    def options(self, credential: Credential) -> JsonDocument:
        return self.call(credential, 'GET', '/arcopen/fn/archive/type/list')

    def works(self, credential: Credential, cursor: str = '', limit: int = 20) -> JsonDocument:
        if cursor and (len(cursor) > 10 or not cursor.isdecimal() or not 1 <= int(cursor) <= 2147483647): raise ChannelError('bilibili-open', 'page_invalid')
        if not 1 <= limit <= 100: raise ChannelError('bilibili-open', 'limit_invalid')
        value = self.call(credential, 'GET', '/arcopen/fn/archive/viewlist', params={'ps': str(limit), 'pn': cursor or '1'})
        page = _document(value.get('page'))
        number, total = int(page.get('pn') or cursor or 1), int(page.get('total') or 0)
        return {**value, 'items': _rows(value.get('list')), 'next_cursor': str(number + 1) if number * limit < total else ''}

    def work(self, credential: Credential, work_id: str) -> JsonDocument:
        value = self.call(credential, 'GET', '/arcopen/fn/archive/view', params={'resource_id': work_id})
        if not value.get('resource_id'): raise ChannelError('bilibili-open', 'work_identity_missing', status=502)
        return {**value, 'id': value['resource_id'], 'description': value.get('desc'),
            'created_at': value.get('ptime') or value.get('ctime'), 'author_id': credential.account_uid,
            'permalink': _document(value.get('video_info')).get('share_url')}

    def initialize_upload(self, credential: Credential, path: Path) -> PublishReceipt:
        validate_local_media('bilibili-open', path, 'video', {})
        value = self.call(credential, 'POST', '/arcopen/fn/archive/video/init', body={'name': path.name, 'utype': '0'})
        token = str(value.get('upload_token') or '')
        if not token: raise ChannelError('bilibili-open', 'upload_identity_missing', status=502)
        return PublishReceipt(state='requires_upload', external_request_id=token, details={'stage': 'media_upload', 'size': path.stat().st_size})

    def upload(self, credential: Credential, receipt: PublishReceipt, path: Path) -> PublishReceipt:
        if receipt.state != 'requires_upload' or receipt.details.get('size') != path.stat().st_size: raise ChannelError('bilibili-open', 'upload_state_invalid')
        size, chunk = path.stat().st_size, 100 * 1024**2
        for index, first in enumerate(range(0, size, chunk), 1):
            length = min(chunk, size - first)
            with FileRange(path, first, length) as stream:
                value = self.http.send('POST', 'https://openupos.bilivideo.com/video/v2/part/upload',
                    params={'upload_token': receipt.external_request_id, 'part_number': str(index)},
                    headers={'Content-Type': 'application/json', 'Content-Length': str(length)}, content=iter(lambda: stream.read(1024**2), b'')).data
            if value.get('code') not in (0, '0', None): raise ChannelError('bilibili-open', 'video_part_rejected', status=502)
        self.call(credential, 'POST', '/arcopen/fn/archive/video/complete', params={'upload_token': receipt.external_request_id})
        return receipt.model_copy(update={'state': 'requires_finalize', 'details': {**receipt.details, 'stage': 'media_ready', 'media_ready': True}})

    def publish(self, credential: Credential, content: PublishContent, cover_path: Path | None = None) -> PublishReceipt:
        token = content.media[0].media_id if content.media else ''
        if not token: raise ChannelError('bilibili-open', 'uploaded_media_id_required')
        options = dict(content.options)
        if type(options.get('tid')) is not int or options['tid'] <= 0: raise ChannelError('bilibili-open', 'archive_type_required')
        tags = parse_topics(content.body)
        if not tags or native_text_length(','.join(tags)) > 199: raise ChannelError('bilibili-open', 'archive_tags_invalid')
        body = {'title': content.title or 'Untitled', 'tid': options['tid'], 'tag': ','.join(tags),
            'copyright': options.get('copyright', 1), 'no_reprint': options.get('no_reprint', 0),
            'desc': strip_topics(content.body),
            **{key: options[key] for key in ('source', 'topic_id', 'mission_id') if key in options}}
        if cover_path:
            validate_local_cover('bilibili-open', cover_path)
            with cover_path.open('rb') as stream:
                value = self.call(credential, 'POST', '/arcopen/fn/archive/cover/upload', files={'file': (cover_path.name, stream, 'image/jpeg')})
            if not value.get('url'): raise ChannelError('bilibili-open', 'cover_upload_failed', status=502)
            body['cover'] = value['url']
        result = self.call(credential, 'POST', '/arcopen/fn/archive/add-by-utoken', params={'upload_token': token}, body=body)
        identity = str(result.get('resource_id') or '')
        if not identity: raise ChannelError('bilibili-open', 'publication_identity_missing', status=502)
        return PublishReceipt(state='pending', external_request_id=identity)

    def finalize(self, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
        if receipt.details.get('stage') == 'media_ready': return receipt.model_copy(update={'state': 'requires_finalize'})
        value = self.work(credential, receipt.external_request_id)
        state = _document(value.get('addit_info')).get('state')
        identity = str(value.get('resource_id') or '')
        if state != 0 or not re.fullmatch(r'(?:BV[0-9A-Z]+|av\d+)', identity, re.IGNORECASE):
            return receipt.model_copy(update={'state': 'pending', 'details': {**receipt.details, 'review_state': state}})
        return receipt.model_copy(update={'state': 'published', 'platform_work_id': identity, 'permalink': f'https://www.bilibili.com/video/{identity}'})
