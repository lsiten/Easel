"""Registered Douyin mini-app QR, ticket, identity, fans, and video query APIs."""
from collections.abc import Mapping, Sequence

import httpx

from easel.channels.app_credentials import AppCredentials
from easel.channels.http import ProviderHttp
from easel.channels.models import ChannelError, JsonDocument
from easel.channels.native_config import native_setting
from easel.channels.reads import _document, _rows
from easel.channels.store import CredentialStore
from easel.channels.token_values import token_seconds


class DouyinMiniApp:
    def __init__(self, settings: Mapping[str, str], *, transport: httpx.BaseTransport | None = None, store: CredentialStore | None = None) -> None:
        self.client_id = native_setting(settings, 'douyin-open', 'MINIAPP_CLIENT_ID')
        self.secret = native_setting(settings, 'douyin-open', 'MINIAPP_CLIENT_SECRET')
        if not self.client_id or not self.secret: raise ChannelError('douyin-open', 'miniapp_not_configured', status=409)
        self.sandbox = native_setting(settings, 'douyin-open', 'MINIAPP_SANDBOX').lower() in {'1', 'true', 'yes'}
        self.base = 'https://open-sandbox.douyin.com' if self.sandbox else 'https://open.douyin.com'
        self.http = ProviderHttp('douyin-open', transport=transport)
        self.cache = AppCredentials('douyin-open', self.client_id, self.secret, store)

    def request(self, method: str, path: str, *, params=None, body=None, data=None, token: str = '') -> JsonDocument:
        url = path if path.startswith('https://') else self.base + path
        response = self.http.send(method, url, params=params, body=body, data=data,
            headers={'access-token': token} if token else {}).data
        value = _document(response.get('data'))
        if value.get('error_code', response.get('error_code', 0)) not in (None, 0, '0'):
            raise ChannelError('douyin-open', 'miniapp_api_error', status=502)
        return value

    def get_client_token(self) -> str:
        def load():
            value = self.request('POST', '/oauth/client_token/', body={'client_key': self.client_id,
                'client_secret': self.secret, 'grant_type': 'client_credential'})
            if not value.get('access_token'): raise ChannelError('douyin-open', 'miniapp_client_token_missing', status=502)
            return value, token_seconds('douyin-open', value.get('expires_in'))
        return str(self.cache.get('miniapp:' + self.base, load)['access_token'])

    def create_qr_code(self, path: str, *, width: int = 430, app_name: str = 'douyin', is_circle_code: bool = False) -> str:
        if not path or type(width) is not int or width <= 0: raise ChannelError('douyin-open', 'qr_options_invalid')
        value = self.request('POST', '/api/apps/v1/qrcode/create/', token=self.get_client_token(), body={
            'app_name': app_name, 'appid': self.client_id, 'path': path, 'width': width, 'is_circle_code': is_circle_code})
        image = value.get('img')
        if not isinstance(image, str) or not image: raise ChannelError('douyin-open', 'qr_image_missing', status=502)
        return image if image.startswith('data:') else 'data:image/png;base64,' + image

    def code_to_session(self, code: str) -> JsonDocument:
        endpoint = self.base + '/api/apps/v2/jscode2session' if self.sandbox else 'https://developer.toutiao.com/api/apps/v2/jscode2session'
        value = self.request('POST', endpoint, body={'appid': self.client_id, 'secret': self.secret, 'code': code})
        if not isinstance(value.get('openid'), str) or not value.get('openid'): raise ChannelError('douyin-open', 'miniapp_identity_missing', status=502)
        return value

    def get_user_access_token(self, ticket: str) -> JsonDocument:
        value = self.request('POST', '/oauth/access_token/', data={'client_key': self.client_id,
            'client_secret': self.secret, 'code': ticket, 'grant_type': 'authorization_code'})
        if not value.get('access_token') or not value.get('open_id'): raise ChannelError('douyin-open', 'miniapp_user_token_invalid', status=502)
        return value

    def get_fans_data(self, access_token: str, open_id: str, *, date_type: int = 7) -> JsonDocument:
        if date_type not in {7, 15}: raise ChannelError('douyin-open', 'fans_date_type_invalid')
        value = self.request('GET', '/api/apps/v1/user/get_fans/', token=access_token, params={'open_id': open_id, 'date_type': str(date_type)})
        records = _rows(_document(value.get('data')).get('result_list') or value.get('result_list'))
        return {'scope': 'ma.user.data', 'date_type': date_type,
            'fans_count': records[-1].get('total_fans') if records else None, 'fans': records}

    def query_video_data(self, access_token: str, open_id: str, *, item_ids: Sequence[str] = (), video_ids: Sequence[str] = ()) -> JsonDocument:
        if not item_ids and not video_ids: raise ChannelError('douyin-open', 'video_identity_required')
        return self.request('POST', '/api/apps/v1/video/query/', token=access_token, params={'open_id': open_id},
            body={**({'item_ids': list(item_ids)} if item_ids else {}), **({'video_ids': list(video_ids)} if video_ids else {})})

    def video_id_to_open_item_id(self, video_ids: Sequence[str]) -> JsonDocument:
        value = self.request('POST', '/api/apps/v1/convert_video_id/video_id_to_open_item_id/', token=self.get_client_token(),
            body={'access_key': self.client_id, 'app_id': self.client_id, 'video_ids': list(video_ids)})
        result = value.get('convert_result')
        if isinstance(result, dict): return result
        return {str(row.get('_key') or row.get('key') or row.get('video_id')):
            str(row.get('_val') or row.get('value') or row.get('item_id')) for row in _rows(result)
            if (row.get('_key') or row.get('key') or row.get('video_id')) and (row.get('_val') or row.get('value') or row.get('item_id'))}
