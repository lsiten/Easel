"""Registered native OAuth protocols and mini-app single-use state exchange."""
import time
from urllib.parse import urlencode

from pydantic import SecretStr

from easel.channels.definitions import definition
from easel.channels.douyin_miniapp import DouyinMiniApp
from easel.channels.http import ProviderHttp
from easel.channels.models import AccountProfile, ChannelError, Credential, JsonDocument, OAuthApplication, OAuthPending


def start_native(service, persona: str, platform: str, application: OAuthApplication) -> JsonDocument:
    spec = definition(platform)
    if spec.auth_mode == 'app_handoff': raise ChannelError(platform, 'verified_account_binding_required', status=409)
    qr_mode = platform == 'douyin-open' and service.settings.get('EASEL_DOUYIN_OPEN_AUTH_TYPE', 'qr').lower() != 'oauth2'
    pending = OAuthPending(persona=persona, platform=platform, verifier=SecretStr('miniapp_qr' if qr_mode else ''),
        redirect_uri=application.redirect_uri, expires_at=time.time() + 600)
    state = service.store.issue_state(pending)
    if qr_mode:
        try:
            image = DouyinMiniApp(service.settings, transport=service.transport, store=service.store).create_qr_code('pages/index/index?authType=channel&state=' + state)
        except ChannelError:
            service.store.consume_state(state, platform)
            service.store.fail_session(state, platform)
            raise
        return {'authorization_url': image, 'qr_code': image, 'state': state, 'expires_at': pending.expires_at,
            'platform': platform, 'persona': persona, 'auth_mode': 'miniapp_qr'}
    from urllib.parse import urlsplit
    parsed = urlsplit(application.redirect_uri)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}):
        service.store.consume_state(state, platform)
        service.store.fail_session(state, platform)
        raise ChannelError(platform, 'redirect_uri_invalid')
    params = {'response_type': 'code', 'state': state, 'redirect_uri': application.redirect_uri,
        'scope': ','.join(application.scopes or spec.scopes)}
    match platform:
        case 'bilibili-open': params.update(client_id=application.client_id, gourl=params.pop('redirect_uri'))
        case 'kwai-open': params.update(app_id=application.client_id, ua='pc')
        case 'douyin-open': params.update(client_key=application.client_id)
        case 'wechat-official-open': params.update(appid=application.client_id)
    url = spec.authorize_url + '?' + urlencode(params)
    if platform == 'wechat-official-open': url += '#wechat_redirect'
    return {'authorization_url': url, 'state': state, 'expires_at': pending.expires_at, 'platform': platform, 'persona': persona, 'auth_mode': 'oauth2'}


def native_token(platform: str, application: OAuthApplication, params, transport=None) -> JsonDocument:
    http = ProviderHttp(platform, transport=transport)
    data = dict(params)
    refresh = data.get('grant_type') == 'refresh_token'
    match platform:
        case 'bilibili-open':
            data.update(client_id=application.client_id, client_secret=application.client_secret.get_secret_value())
            path = '/x/account-oauth2/v1/refresh_token' if refresh else '/x/account-oauth2/v1/token'
            value = http.send('POST', 'https://api.bilibili.com' + path, data=data).data
            if value.get('code') not in (0, '0'): raise ChannelError(platform, 'token_response_invalid', status=502)
            payload = value.get('data')
        case 'kwai-open':
            data.update(app_id=application.client_id, app_secret=application.client_secret.get_secret_value())
            data.pop('redirect_uri', None)
            path = '/oauth2/refresh_token' if refresh else '/oauth2/access_token'
            payload = http.send('GET', 'https://open.kuaishou.com' + path, params=data).data
            if payload.get('result') != 1: raise ChannelError(platform, 'token_response_invalid', status=502)
        case 'douyin-open':
            data.update(client_key=application.client_id)
            if not refresh: data['client_secret'] = application.client_secret.get_secret_value()
            data.pop('redirect_uri', None)
            path = '/oauth/refresh_token/' if refresh else '/oauth/access_token/'
            value = http.send('POST', 'https://open.douyin.com' + path, data=data).data
            payload = value.get('data')
        case 'wechat-official-open':
            data.update(appid=application.client_id)
            if not refresh: data['secret'] = application.client_secret.get_secret_value()
            data.pop('redirect_uri', None)
            path = '/sns/oauth2/refresh_token' if refresh else '/sns/oauth2/access_token'
            payload = http.send('GET', 'https://api.weixin.qq.com' + path, params=data).data
        case _: raise ChannelError(platform, 'oauth_unsupported', status=405)
    if not isinstance(payload, dict) or payload.get('error_code') not in (None, 0, '0') or payload.get('errcode') not in (None, 0, '0'):
        raise ChannelError(platform, 'token_response_invalid', status=502)
    if payload.get('openid'): payload = {**payload, 'open_id': payload['openid']}
    return payload


def exchange_miniapp(service, state: str, token: str, tickets: dict[str, str], *, nickname: str = '', avatar: str = '', validate_persona=lambda _: True) -> Credential:
    pending = service.store.consume_state(state, 'douyin-open')
    try:
        if pending.verifier.get_secret_value() != 'miniapp_qr' or not validate_persona(pending.persona): raise ChannelError('douyin-open', 'oauth_scope_invalid', status=403)
        miniapp = DouyinMiniApp(service.settings, transport=service.transport, store=service.store)
        uid = str(miniapp.code_to_session(token)['openid'])
        home_ticket, video_ticket = tickets.get('ma.user.data'), tickets.get('ma.video.bind')
        if not home_ticket or not video_ticket: raise ChannelError('douyin-open', 'miniapp_tickets_missing')
        home = miniapp.get_user_access_token(home_ticket)
        video = home if home_ticket == video_ticket else miniapp.get_user_access_token(video_ticket)
        if str(home['open_id']) != uid or str(video['open_id']) != uid: raise ChannelError('douyin-open', 'miniapp_identity_mismatch', status=403)
        if 'ma.user.data' not in str(home.get('scope') or '').replace(',', ' ').split() or 'ma.video.bind' not in str(video.get('scope') or '').replace(',', ' ').split():
            raise ChannelError('douyin-open', 'miniapp_scope_missing', status=403)
        credential = service._credential('douyin-open', pending.persona, video)
        profile = AccountProfile(account_uid=uid, display_name=nickname or uid, avatar_url=avatar,
            extra={'auth_mode': 'miniapp_qr', 'client_id': miniapp.client_id})
        credential = credential.model_copy(update={'account_uid': uid, 'display_name': profile.display_name, 'profile': profile,
            'auxiliary_tokens': {'homepage': home}})
        service.store.save(credential, oauth_state=state)
        return credential
    except ChannelError:
        service.store.fail_session(state, 'douyin-open')
        raise
