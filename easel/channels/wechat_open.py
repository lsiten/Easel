"""WeChat official articles and authenticated Channels work ownership."""
from collections.abc import Mapping, Sequence
import hashlib
import re
from pathlib import Path
import time

import httpx

from easel.channels.app_credentials import AppCredentials
from easel.channels.http import ProviderHttp
from easel.channels.models import AccountProfile, ChannelError, Credential, JsonDocument, PublishContent, PublishReceipt
from easel.channels.native_config import native_application, native_setting
from easel.channels.reads import ChannelReads, _document
from easel.channels.rules import validate_local_cover, validate_local_media
from easel.channels.token_values import token_seconds


class WechatOpen:
    def __init__(self, platform: str, settings: Mapping[str, str], *, transport: httpx.BaseTransport | None = None, store=None) -> None:
        self.platform = platform
        configured = native_setting(settings, platform, 'CLIENT_ID') and native_setting(settings, platform, 'CLIENT_SECRET')
        self.application = native_application(settings, platform) if configured or platform == 'wechat-official-open' else None
        self.http = ProviderHttp(platform, transport=transport)
        self.cache = AppCredentials(platform, self.application.client_id, self.application.client_secret.get_secret_value(), store) if self.application else None

    def call(self, method: str, path: str, *, params=None, body=None, files=None) -> JsonDocument:
        value = self.http.send(method, 'https://api.weixin.qq.com' + path, params=params, body=body, files=files).data
        if value.get('errcode') not in (None, 0, '0'): raise ChannelError(self.platform, 'wechat_api_error', str(value['errcode']), 502)
        return value

    def app_token(self) -> str:
        if self.application is None or self.cache is None: raise ChannelError(self.platform, 'native_application_not_configured', status=409)
        def load():
            params = {'grant_type': 'client_credential', 'appid': self.application.client_id, 'secret': self.application.client_secret.get_secret_value()}
            value = self.call('POST', '/cgi-bin/token', body=params) if self.platform == 'wechat-channels-open' else self.call('GET', '/cgi-bin/token', params=params)
            if not value.get('access_token'): raise ChannelError(self.platform, 'application_token_missing', status=502)
            return value, token_seconds(self.platform, value.get('expires_in'))
        return str(self.cache.get('app_token', load)['access_token'])

    def app_call(self, method: str, path: str, *, body=None, files=None, params=None) -> JsonDocument:
        return self.call(method, path, params={'access_token': self.app_token(), **(params or {})}, body=body, files=files)

    def profile(self, credential: Credential) -> AccountProfile:
        if self.platform == 'wechat-channels-open': return self.cookie_profile(credential)
        publisher = 'appid:' + self.application.client_id
        user_id = credential.parent_uid or credential.account_uid
        value = self.call('GET', '/sns/userinfo', params={'access_token': credential.access_token.get_secret_value(),
            'openid': user_id, 'lang': 'zh_CN'})
        uid = str(value.get('openid') or '')
        if not uid or user_id not in {'pending', uid}: raise ChannelError(self.platform, 'profile_identity_mismatch', status=403)
        self.app_token()
        return AccountProfile(account_uid=publisher, parent_uid=uid, display_name=self.application.client_id,
            extra={'client_id': self.application.client_id, 'publisher_identity': 'configured_official_app',
                'oauth_user_openid': uid, 'oauth_user_nickname': value.get('nickname')})

    def cookie_profile(self, credential: Credential) -> AccountProfile:
        cookie = credential.access_token.get_secret_value()
        values = dict(pair.strip().split('=', 1) for pair in cookie.split(';') if '=' in pair)
        if not values.get('sessionid'): raise ChannelError(self.platform, 'channels_cookie_invalid', status=401)
        stamp = str(int(time.time() * 1000))
        value = self.http.send('POST', 'https://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/auth/auth_data',
            params={'_aid': hashlib.md5(cookie.encode()).hexdigest(), '_rid': hashlib.md5((cookie + ':' + stamp).encode()).hexdigest(),
                '_pageUrl': 'https://channels.weixin.qq.com/platform'},
            headers={'Cookie': cookie, 'Origin': 'https://channels.weixin.qq.com', 'Referer': 'https://channels.weixin.qq.com/platform',
                'X-WECHAT-UIN': values.get('wxuin', ''), 'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'},
            body={'timestamp': stamp, '_log_finder_uin': '', '_log_finder_id': '', 'rawKeyBuff': None,
                'pluginSessionId': None, 'scene': 7, 'reqScene': 7}).data
        code = value.get('errCode', value.get('errcode', value.get('err_code', value.get('code', _document(value.get('base_resp')).get('ret', 0)))))
        user = _document(_document(value.get('data')).get('finderUser'))
        uid = str(user.get('uniqId') or '')
        if code != 0 or not uid or credential.account_uid not in {'pending', uid}: raise ChannelError(self.platform, 'channels_identity_unverified', status=403)
        return AccountProfile(account_uid=uid, display_name=str(user.get('nickname') or uid), avatar_url=str(user.get('headImgUrl') or ''),
            fans_count=ChannelReads._count(user.get('fansCount')), extra={'client_id': self.application.client_id if self.application else '',
                'finder_id': user.get('finderUsername'), 'work_count': user.get('feedsCount')})

    def _assert_app(self, credential: Credential) -> None:
        if self.application is None: raise ChannelError(self.platform, 'native_application_not_configured', status=409)
        if credential.platform != self.platform or credential.profile is None or credential.profile.extra.get('client_id') != self.application.client_id:
            raise ChannelError(self.platform, 'application_owner_mismatch', status=403)
        if self.platform == 'wechat-official-open' and credential.account_uid != 'appid:' + self.application.client_id:
            raise ChannelError(self.platform, 'publisher_identity_mismatch', status=403)

    def work(self, credential: Credential, work_id: str) -> JsonDocument:
        self._assert_app(credential)
        finder, separator, video = work_id.partition(':')
        owned = credential.profile.extra.get('finder_id')
        if not separator or not owned or finder != owned: raise ChannelError(self.platform, 'work_owner_mismatch', status=403)
        value = self.app_call('POST', '/channels/ec/finder/video/get', body={'finder_id': finder, 'video_id': video})
        detail = _document(value.get('data')) or value
        if detail.get('finder_id') != finder: raise ChannelError(self.platform, 'work_owner_unverified', status=403)
        return {**detail, 'id': work_id, 'title': detail.get('title') or detail.get('description'),
            'created_at': detail.get('publish_time') or detail.get('create_time'), 'author_id': credential.account_uid}

    def resolve_link(self, credential: Credential, link: str) -> JsonDocument:
        from urllib.parse import urlsplit
        if urlsplit(link).hostname not in {'channels.weixin.qq.com', 'weixin.qq.com'}: raise ChannelError(self.platform, 'work_link_invalid')
        value = self.app_call('POST', '/channels/ec/finder/link/info', body={'link': link})
        value = _document(value.get('data')) or value
        work_id = str(value.get('finder_id') or '') + ':' + str(value.get('video_id') or '')
        return {'platform_work_id': work_id, 'permalink': link, 'work': self.work(credential, work_id)}

    def publish(self, credential: Credential, content: PublishContent, *, media_paths: Sequence[Path] = (), cover_path: Path | None = None) -> PublishReceipt:
        self._assert_app(credential)
        cover = cover_path or (media_paths[0] if media_paths else None)
        if not cover: raise ChannelError(self.platform, 'article_cover_required')
        validate_local_cover(self.platform, cover)
        body = content.body
        for index, path in enumerate(media_paths):
            validate_local_media(self.platform, path, 'image', {})
            with path.open('rb') as stream:
                image = self.app_call('POST', '/cgi-bin/media/uploadimg', files={'media': (path.name, stream, 'image/jpeg')})
            url = str(image.get('url') or '')
            if not url: raise ChannelError(self.platform, 'inline_image_missing', status=502)
            source = content.media[index].url if index < len(content.media) else ''
            body = body.replace(source, url) if source and source in body else body + '<p><img src="' + url + '"/></p>'
        with cover.open('rb') as stream:
            thumbnail = self.app_call('POST', '/cgi-bin/material/add_material', params={'type': 'image'}, files={'media': (cover.name, stream, 'image/jpeg')})
        if not thumbnail.get('media_id'): raise ChannelError(self.platform, 'thumbnail_identity_missing', status=502)
        options = content.options
        article = {'article_type': 'news', 'title': content.title, 'content': body, 'thumb_media_id': thumbnail['media_id'],
            'show_cover_pic': int(bool(options.get('showCoverPic', True))), 'digest': options.get('digest', re.sub('<[^>]+>', '', body)[:120]),
            'content_source_url': options.get('sourceUrl', ''), **{key: options[key] for key in ('author', 'open_comment', 'only_fans_can_comment') if key in options}}
        result = self.app_call('POST', '/cgi-bin/draft/add', body={'articles': [article]})
        if not result.get('media_id'): raise ChannelError(self.platform, 'draft_identity_missing', status=502)
        return PublishReceipt(state='requires_finalize', external_request_id=str(result['media_id']), details={'stage': 'article_draft'})

    def finalize(self, credential: Credential, receipt: PublishReceipt) -> PublishReceipt:
        self._assert_app(credential)
        if receipt.details.get('stage') == 'article_draft':
            value = self.app_call('POST', '/cgi-bin/freepublish/submit', body={'media_id': receipt.external_request_id})
            if not value.get('publish_id'): raise ChannelError(self.platform, 'publication_identity_missing', status=502)
            return PublishReceipt(state='pending', external_request_id=str(value['publish_id']), details={'stage': 'article_publish'})
        value = self.app_call('POST', '/cgi-bin/freepublish/get', body={'publish_id': receipt.external_request_id})
        status = value.get('publish_status')
        if type(status) is not int or status < 0: raise ChannelError(self.platform, 'publication_status_invalid', status=502)
        if status == 1: return receipt
        if status != 0: return receipt.model_copy(update={'state': 'failed', 'details': {**receipt.details, 'provider_status': status}})
        rows = _document(value.get('article_detail')).get('item')
        url = str(rows[0].get('article_url') or '') if isinstance(rows, list) and rows and isinstance(rows[0], dict) else ''
        identity = str(value.get('article_id') or '')
        if not identity or not url: raise ChannelError(self.platform, 'article_identity_missing', status=502)
        return receipt.model_copy(update={'state': 'published', 'platform_work_id': identity, 'permalink': url})
