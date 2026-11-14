"""Native protocol entry points enforce state, scope, and secret boundaries."""
import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from easel.channels import ChannelAdapter, CredentialStore, OAuthService
from easel.channels.native_webhooks import load_native_webhook_events
from easel.channels.router import create_router
from easel.channels.models import Credential
from pydantic import SecretStr


def client(root, config):
    app = FastAPI()
    app.include_router(create_router(lambda: root, lambda: config, lambda value: value == 'alice', lambda _: None))
    return TestClient(app)


def test_miniapp_qr_generic_post_callback_is_one_use_and_scope_checked(tmp_path, monkeypatch):
    from easel.channels import router
    config = {'EASEL_DOUYIN_OPEN_MINIAPP_CLIENT_ID': 'mini', 'EASEL_DOUYIN_OPEN_MINIAPP_CLIENT_SECRET': 'secret'}
    def respond(request):
        path = request.url.path
        if path == '/oauth/client_token/': value = {'access_token': 'client', 'expires_in': 3600}
        elif path.endswith('/qrcode/create/'): value = {'img': 'aW1hZ2U='}
        elif path.endswith('/jscode2session'): value = {'openid': 'owner'}
        elif path == '/oauth/access_token/': value = {'open_id': 'owner', 'access_token': 'access', 'scope': 'ma.user.data ma.video.bind', 'expires_in': 3600}
        else: raise AssertionError(request.url)
        return httpx.Response(200, json={'data': value})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(router, 'OAuthService', lambda *args, **kwargs: OAuthService(*args, transport=transport, **kwargs))
    api = client(tmp_path, config)
    started = api.post('/api/channels/douyin-open/auth/start', json={'persona': 'alice'})
    assert started.status_code == 200 and started.json()['auth_mode'] == 'miniapp_qr'
    callback = {'state': started.json()['state'], 'token': 'code', 'tickets': {'ma.user.data': 'ticket', 'ma.video.bind': 'ticket'}}
    result = api.post('/api/channels/douyin-open/auth/callback', json=callback)
    assert result.status_code == 200 and result.json()['account_uid'] == 'owner'
    replay = api.post('/api/channels/douyin-open/auth/callback', json=callback)
    assert replay.status_code == 403
    status = api.get('/api/channels/douyin-open/auth/status', params={'persona': 'alice', 'state': callback['state']})
    assert status.json()['status'] == 'connected'
    denied = api.get('/api/channels/douyin-open/auth/status', params={'persona': 'bob', 'state': callback['state']})
    assert denied.status_code == 400
    accounts = api.get('/api/channels/douyin-open/accounts', params={'persona': 'alice'})
    assert 'access_token' not in accounts.text and 'auxiliary_tokens' not in accounts.text


def test_channels_cookie_binding_requires_live_provider_identity_before_persistence(tmp_path, monkeypatch):
    from easel.channels import native_router
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={'data': {'finderUser': {'uniqId': 'actual-owner', 'finderUsername': 'finder', 'nickname': 'Creator'}}, 'errCode': 0})
    monkeypatch.setattr(native_router, 'ChannelAdapter', lambda *args, **kwargs: ChannelAdapter(*args, transport=httpx.MockTransport(respond), **kwargs))
    api = client(tmp_path, {})
    route = '/api/channels/wechat-channels-open/accounts/bind-cookie'
    invalid = api.post(route, json={'persona': 'alice', 'cookie': 'sessionid=secret', 'account_uid': 'claimed-owner'})
    assert invalid.status_code == 422 and not seen
    bound = api.post(route, json={'persona': 'alice', 'cookie': 'sessionid=secret'})
    assert bound.status_code == 200 and bound.json()['account_uid'] == 'actual-owner'
    saved = CredentialStore(tmp_path / '_channels').get('alice', 'wechat-channels-open', 'actual-owner')
    assert saved.access_token.get_secret_value() == 'sessionid=secret'
    assert b'sessionid=secret' not in (tmp_path / '_channels/channels.sqlite3').read_bytes()


@pytest.mark.parametrize('platform,link,identity,canonical', [
    ('bilibili-open', 'https://m.bilibili.com/video/BV123', 'BV123', 'https://www.bilibili.com/video/BV123'),
    ('douyin-open', 'https://www.douyin.com/?modal_id=123', '123', 'https://www.douyin.com/video/123'),
    ('kwai-open', 'https://c.kuaishou.com/photo/123', '123', 'https://www.kuaishou.com/short-video/123'),
    ('rednote-link', 'https://www.rednote.com/explore/abc?xsec_token=valid', 'abc', 'https://www.rednote.com/explore/abc?xsec_token=valid'),
])
def test_native_link_info_is_accessible_without_fabricated_account(tmp_path, platform, link, identity, canonical):
    api = client(tmp_path, {})
    result = api.get(f'/api/channels/{platform}/work-link', params={'persona': 'alice', 'link': link})
    assert result.status_code == 200
    assert result.json()['platform_work_id'] == identity and result.json()['permalink'] == canonical
    assert result.json()['ownership_verified'] is False


def test_native_signed_callback_challenge_and_fresh_unbound_inbox(tmp_path):
    config = {'EASEL_DOUYIN_OPEN_CLIENT_ID': 'app', 'EASEL_DOUYIN_OPEN_CLIENT_SECRET': 'secret'}
    api = client(tmp_path, config)
    route = '/api/channels/douyin-open/webhook'
    def send(payload, valid=True):
        body = json.dumps(payload).encode()
        signature = hmac.new(b'secret', body, hashlib.sha256).hexdigest() if valid else 'bad'
        return api.post(route, content=body, headers={'x-douyin-signature': signature, 'content-type': 'application/json'})
    challenge = send({'event': 'verify_webhook', 'content': {'challenge': 'challenge'}})
    assert challenge.status_code == 200 and challenge.json() == {'challenge': 'challenge'}
    payload = {'event': 'create_video', 'content': {'share_id': 'share', 'video_id': '123'}}
    assert send(payload, False).status_code == 403
    assert send(payload).status_code == 200 and send(payload).status_code == 200
    events = load_native_webhook_events(CredentialStore(tmp_path / '_channels'))
    assert len(events) == 1 and events[0]['candidate_ids'] == ['share', '123']


def test_bilibili_registered_category_source_uses_configured_adapter(tmp_path, monkeypatch):
    from easel.channels import router
    config = {'EASEL_BILIBILI_OPEN_CLIENT_ID': 'app', 'EASEL_BILIBILI_OPEN_CLIENT_SECRET': 'secret'}
    store = CredentialStore(tmp_path / '_channels')
    store.save(Credential(persona='alice', platform='bilibili-open', account_uid='owner', access_token=SecretStr('token')))
    def respond(request):
        assert request.url.path == '/arcopen/fn/archive/type/list'
        assert request.headers['Access-Token'] == 'token' and request.headers['x-bili-accesskeyid'] == 'app'
        return httpx.Response(200, json={'code': 0, 'data': [{'id': 17, 'name': 'Game', 'children': []}]})
    monkeypatch.setattr(router, 'ChannelAdapter', lambda *args, **kwargs: ChannelAdapter(*args, transport=httpx.MockTransport(respond), **kwargs))
    api = client(tmp_path, config)
    result = api.get('/api/channels/bilibili-open/publish-options/tid/values', params={'persona': 'alice', 'account_uid': 'owner'})
    assert result.status_code == 200 and result.json()['items'][0]['id'] == 17


def test_rednote_data_id_fallback_remains_unverified_reference(tmp_path):
    api = client(tmp_path, {})
    result = api.get('/api/channels/rednote-link/work-link', params={'persona': 'alice', 'link': 'https://www.xiaohongshu.com/', 'data_id': 'known-note'})
    assert result.status_code == 200
    assert result.json()['platform_work_id'] == 'known-note' and result.json()['link_status'] == 'pending'
    assert result.json()['ownership_verified'] is False
