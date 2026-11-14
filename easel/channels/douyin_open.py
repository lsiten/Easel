"""Douyin app share IDs, open tickets, and signed native handoff schemas."""
from collections.abc import Mapping, Sequence
import hashlib
import json
import secrets
import string
import time
from urllib.parse import quote, urlencode, urlsplit

import httpx

from easel.channels.app_credentials import AppCredentials
from easel.channels.http import ProviderHttp
from easel.channels.models import ChannelError, JsonDocument
from easel.channels.native_config import native_application
from easel.channels.reads import _document
from easel.channels.store import CredentialStore
from easel.channels.token_values import token_seconds


class DouyinOpen:
    def __init__(self, settings: Mapping[str, str], *, transport: httpx.BaseTransport | None = None, store: CredentialStore | None = None) -> None:
        self.application = native_application(settings, 'douyin-open')
        self.http = ProviderHttp('douyin-open', transport=transport)
        self.cache = AppCredentials('douyin-open', self.application.client_id, self.application.client_secret.get_secret_value(), store)

    def request(self, method: str, path: str, *, params=None, body=None, data=None, headers=None) -> JsonDocument:
        response = self.http.send(method, 'https://open.douyin.com' + path, params=params, body=body, data=data, headers=headers).data
        value = _document(response.get('data'))
        code = value.get('error_code', response.get('error_code', 0))
        if code not in (0, None, '0'): raise ChannelError('douyin-open', 'provider_api_error', str(code), 502)
        return value

    def get_client_token(self) -> str:
        def load():
            value = self.request('POST', '/oauth/client_token/', body={'grant_type': 'client_credential',
                'client_key': self.application.client_id, 'client_secret': self.application.client_secret.get_secret_value()})
            if not isinstance(value.get('access_token'), str) or not value.get('access_token'): raise ChannelError('douyin-open', 'client_token_missing', status=502)
            return value, token_seconds('douyin-open', value.get('expires_in'))
        return str(self.cache.get('client_token', load)['access_token'])

    def get_open_ticket(self) -> str:
        token = self.get_client_token()
        def load():
            value = self.request('GET', '/open/getticket/', headers={'access-token': token})
            if not value.get('ticket'): raise ChannelError('douyin-open', 'open_ticket_missing', status=502)
            return value, token_seconds('douyin-open', value.get('expires_in'))
        key = 'ticket:' + hashlib.sha256(token.encode()).hexdigest()
        return str(self.cache.get(key, load)['ticket'])

    def _share(self, params: Mapping[str, str]) -> JsonDocument:
        try:
            return self.request('POST', '/share-id/', params=params, headers={'access-token': self.get_client_token()})
        except ChannelError as error:
            if error.detail not in {'10008', '2190008', '28001003', '28001008'}: raise
            self.cache.clear()
            return self.request('POST', '/share-id/', params=params, headers={'access-token': self.get_client_token()})

    def get_share_id(self) -> str:
        value = self._share({'need_callback': 'true', 'default_hashtag': 'hashtag'})
        share_id = value.get('share_id')
        if not isinstance(share_id, str) or not share_id: raise ChannelError('douyin-open', 'share_identity_missing', status=502)
        return share_id

    def get_share_publish_result(self, share_id: str) -> JsonDocument:
        if not share_id: raise ChannelError('douyin-open', 'share_identity_required')
        value = self._share({'share_id': share_id})
        if value.get('share_id') not in (None, '', share_id): raise ChannelError('douyin-open', 'share_identity_mismatch', status=502)
        for key in ('video_id', 'item_id', 'open_id'):
            identity = value.get(key)
            if identity not in (None, '') and (type(identity) not in (str, int) or not str(identity).strip() or len(str(identity)) > 512):
                raise ChannelError('douyin-open', 'share_result_identity_invalid', status=502)
        return {'share_id': share_id, 'video_id': value.get('video_id'), 'item_id': value.get('item_id'),
            'share_url': value.get('share_url'), 'open_id': value.get('open_id'), 'raw': value}

    def generate_schema(self, options: JsonDocument) -> str:
        ticket = self.get_open_ticket()
        nonce = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        timestamp = str(int(time.time()))
        signature = hashlib.md5(f'nonce_str={nonce}&ticket={ticket}&timestamp={timestamp}'.encode()).hexdigest()
        params = {'client_key': self.application.client_id, 'nonce_str': nonce, 'timestamp': timestamp,
            'signature': signature, 'share_type': 'h5'}
        if options.get('shareId'): params['state'] = str(options['shareId'])
        for key in ('title', 'short_title', 'video_path', 'download_type', 'private_status', 'cover_tsp'):
            if key in options and options[key] is not None: params[key] = str(options[key])
        for key in ('image_list_path', 'title_hashtag_list'):
            if options.get(key): params[key] = json.dumps(options[key], ensure_ascii=False, separators=(',', ':'))
        if options.get('video_path'): params['share_to_publish'] = '1'
        return 'snssdk1128://openplatform/share?' + urlencode(params, quote_via=quote)

    def generate_handoff(self, *, title: str = '', video_url: str = '', image_urls: Sequence[str] = (), options: JsonDocument | None = None) -> JsonDocument:
        if bool(video_url) == bool(image_urls): raise ChannelError('douyin-open', 'handoff_media_invalid')
        for url in ([video_url] if video_url else image_urls):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password: raise ChannelError('douyin-open', 'handoff_media_url_invalid')
        share_id = self.get_share_id()
        schema = self.generate_schema({'download_type': 1, 'private_status': 0, **(options or {}), 'shareId': share_id, 'title': title,
            **({'video_path': video_url} if video_url else {'image_list_path': list(image_urls)})})
        return {'share_id': share_id, 'schema': schema, 'expires_at': time.time() + 3600}
