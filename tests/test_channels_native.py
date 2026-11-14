"""Offline native contracts assert actual wire semantics and identity checkpoints."""
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from easel.channels import ChannelAdapter, CredentialStore, OAuthService
from easel.channels.douyin_open import DouyinOpen
from easel.channels.models import AccountProfile, ChannelError, Credential, MediaInput, OAuthApplication, PublishContent, PublishReceipt
from easel.channels.native_oauth import exchange_miniapp
from easel.channels.router import application_from_settings


def credential(platform, uid='owner'):
    return Credential(persona='alice', platform=platform, account_uid=uid, access_token=SecretStr('user-token'))


def settings(platform):
    prefix = 'EASEL_' + platform.upper().replace('-', '_') + '_'
    return {prefix + 'CLIENT_ID': 'app', prefix + 'CLIENT_SECRET': 'secret', prefix + 'REDIRECT_URI': 'http://localhost/callback'}


@pytest.mark.parametrize('platform,tokenpath,tokenbody,profilepath,profilebody', [
    ('bilibili-open', '/x/account-oauth2/v1/token', {'code': 0, 'data': {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 3600}},
     '/arcopen/fn/user/account/info', {'code': 0, 'data': {'openid': 'owner', 'name': 'Alice'}}),
    ('kwai-open', '/oauth2/access_token', {'result': 1, 'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 3600, 'open_id': 'owner'},
     '/openapi/user_info', {'result': 1, 'user_info': {'name': 'Alice', 'fan': 0}}),
    ('douyin-open', '/oauth/access_token/', {'data': {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 3600, 'open_id': 'owner'}},
     '/oauth/userinfo/', {'data': {'open_id': 'owner', 'nickname': 'Alice'}}),
])
def test_native_oauth_protocol_and_single_use(tmp_path, platform, tokenpath, tokenbody, profilepath, profilebody):
    config = {**settings(platform), 'EASEL_DOUYIN_OPEN_AUTH_TYPE': 'oauth2'}
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path == tokenpath:
            if platform == 'kwai-open':
                assert request.method == 'GET' and request.url.params['app_secret'] == 'secret'
            else:
                values = parse_qs(request.content.decode())
                assert values['grant_type'] == ['authorization_code'] and values['code'] == ['code']
            return httpx.Response(200, json=tokenbody)
        if request.url.path == profilepath: return httpx.Response(200, json=profilebody)
        if request.url.path == '/arcopen/fn/data/user/stat': return httpx.Response(200, json={'code': 0, 'data': {'follower': 0}})
        raise AssertionError(request.url)
    store = CredentialStore(tmp_path)
    service = OAuthService(store, lambda provider: application_from_settings(provider, config), settings=config, transport=httpx.MockTransport(respond))
    started = service.start('alice', platform)
    params = parse_qs(urlsplit(started['authorization_url']).query)
    assert params['state'] == [started['state']]
    value = service.exchange(platform, 'code', started['state'])
    assert value.account_uid == 'owner' and store.get('alice', platform, 'owner').access_token.get_secret_value() == 'access'
    before = len(calls)
    with pytest.raises(ChannelError): service.exchange(platform, 'code', started['state'])
    assert len(calls) == before


def test_bilibili_signed_upload_submission_waits_for_public_identity(tmp_path):
    media = tmp_path / 'clip.mp4'
    media.write_bytes(b'video-data')
    seen, ready = [], [False, True]
    def respond(request):
        seen.append(request)
        path = request.url.path
        if request.url.host == 'member.bilibili.com':
            names = ('x-bili-accesskeyid', 'x-bili-content-md5', 'x-bili-signature-method', 'x-bili-signature-nonce', 'x-bili-signature-version', 'x-bili-timestamp')
            assert request.headers['x-bili-content-md5'] == hashlib.md5(request.content).hexdigest()
            expected = hmac.new(b'secret', '\n'.join(name + ':' + request.headers[name] for name in names).encode(), hashlib.sha256).hexdigest()
            assert request.headers['Authorization'] == expected and request.headers['Access-Token'] == 'user-token'
        if path.endswith('/video/init'): payload = {'upload_token': 'upload'}
        elif path.endswith('/part/upload'):
            assert request.content == b'video-data' and request.url.params['part_number'] == '1'
            assert 'authorization' not in request.headers
            payload = {}
        elif path.endswith('/video/complete'): payload = {}
        elif path.endswith('/add-by-utoken'):
            body = json.loads(request.content)
            assert body['tid'] == 17 and body['tag'] == 'topic' and body['desc'] == 'body'
            payload = {'resource_id': 'private-resource'}
        elif path.endswith('/archive/view'):
            final = ready.pop(0)
            payload = {'resource_id': 'BV1ABC' if final else 'private-resource', 'addit_info': {'state': 0 if final else -1}}
        else: raise AssertionError(path)
        return httpx.Response(200, json={'code': 0, 'data': payload})
    adapter = ChannelAdapter('bilibili-open', settings=settings('bilibili-open'), transport=httpx.MockTransport(respond))
    account = credential('bilibili-open')
    uploaded = adapter.upload(account, adapter.initialize_upload(account, media, 'video'), media)
    assert adapter.finalize(account, uploaded).details['media_ready'] is True
    content = PublishContent(title='Video', body='body #topic #topic', media=(MediaInput(kind='video', media_id='upload'),), options={'tid': 17})
    receipt = adapter.publish(account, content)
    assert adapter.finalize(account, receipt).state == 'pending'
    done = adapter.finalize(account, receipt)
    assert done.state == 'published' and done.platform_work_id == 'BV1ABC'


def test_kwai_fragment_token_and_required_cover(tmp_path):
    video, cover = tmp_path / 'clip.mp4', tmp_path / 'cover.jpg'
    video.write_bytes(b'video')
    cover.write_bytes(b'jpeg')
    calls = []
    def respond(request):
        calls.append(request.url.path)
        if request.url.path == '/openapi/photo/start_upload': value = {'upload_token': 'upload', 'endpoint': 'upload.kuaishou.test'}
        elif request.url.path.startswith('/api/upload/'):
            assert request.url.scheme == 'http' and request.url.params['upload_token'] == 'upload'
            assert 'access_token' not in request.url.params and 'Authorization' not in request.headers
            value = {}
        elif request.url.path == '/openapi/photo/publish':
            assert b'name="cover"' in request.content and b'jpeg' in request.content
            value = {'video_info': {'photo_id': 'photo1'}}
        elif request.url.path == '/openapi/photo/info': value = {'video_info': {'photo_id': 'photo1', 'pending': False, 'create_time': 10}}
        else: raise AssertionError(request.url)
        return httpx.Response(200, json={'result': 1, **value})
    adapter = ChannelAdapter('kwai-open', settings=settings('kwai-open'), transport=httpx.MockTransport(respond))
    account = credential('kwai-open')
    uploaded = adapter.upload(account, adapter.initialize_upload(account, video, 'video'), video)
    assert adapter.finalize(account, uploaded).details['media_ready'] is True
    content = PublishContent(title='Video', media=(MediaInput(kind='video', media_id='upload'),))
    with pytest.raises(ChannelError, match='cover_required'): adapter.publish(account, content)
    done = adapter.finalize(account, adapter.publish(account, content, cover_path=cover))
    assert done.platform_work_id == 'photo1' and calls.count('/openapi/photo/publish') == 1


def test_douyin_handoff_has_real_share_id_signed_schema_and_owned_completion(tmp_path):
    def respond(request):
        if request.url.path == '/oauth/client_token/': value = {'access_token': 'client', 'expires_in': 3600}
        elif request.url.path == '/open/getticket/': value = {'ticket': 'ticket', 'expires_in': 3600}
        elif request.url.params.get('share_id'): value = {'share_id': 'share1', 'video_id': '123', 'open_id': 'other'}
        else: value = {'share_id': 'share1'}
        return httpx.Response(200, json={'data': value})
    adapter = ChannelAdapter('douyin-open', settings=settings('douyin-open'), transport=httpx.MockTransport(respond), store=CredentialStore(tmp_path))
    account = credential('douyin-open')
    receipt = adapter.publish(account, PublishContent(title='Video', body='😀#话题 你好 #话题 #two',
        media=(MediaInput(kind='video', url='https://media.example/clip.mp4'),)))
    assert receipt.state == 'requires_user_action' and receipt.external_request_id == 'share1'
    params = parse_qs(urlsplit(receipt.details['schema']).query)
    expected = hashlib.md5(('nonce_str=' + params['nonce_str'][0] + '&ticket=ticket&timestamp=' + params['timestamp'][0]).encode()).hexdigest()
    assert params['signature'] == [expected] and params['state'] == ['share1']
    assert params['title'] == ['😀 你好'] and params['short_title'] == ['Video']
    assert json.loads(params['title_hashtag_list'][0]) == [{'name': '话题', 'start': 2}, {'name': 'two', 'start': 5}]
    assert adapter.finalize(account, receipt).state == 'requires_user_action'


def test_miniapp_identity_and_ticket_scopes_are_encrypted_and_single_use(tmp_path):
    config = {'EASEL_DOUYIN_OPEN_MINIAPP_CLIENT_ID': 'mini', 'EASEL_DOUYIN_OPEN_MINIAPP_CLIENT_SECRET': 'secret'}
    def respond(request):
        if request.url.path == '/oauth/client_token/': value = {'access_token': 'client', 'expires_in': 3600}
        elif request.url.path.endswith('/qrcode/create/'):
            assert 'state=' in json.loads(request.content)['path']
            value = {'img': 'aW1hZ2U='}
        elif request.url.path.endswith('/jscode2session'): value = {'openid': 'owner'}
        elif request.url.path == '/oauth/access_token/':
            code = parse_qs(request.content.decode())['code'][0]
            value = {'access_token': code + '-secret', 'open_id': 'owner', 'expires_in': 3600, 'scope': 'ma.user.data ma.video.bind'}
        else: raise AssertionError(request.url)
        return httpx.Response(200, json={'data': value})
    store = CredentialStore(tmp_path)
    service = OAuthService(store, lambda platform: application_from_settings(platform, config), settings=config, transport=httpx.MockTransport(respond))
    started = service.start('alice', 'douyin-open')
    assert started['qr_code'].startswith('data:image/png;base64,')
    value = exchange_miniapp(service, started['state'], 'code', {'ma.user.data': 'home', 'ma.video.bind': 'video'})
    assert value.account_uid == 'owner'
    stored = store.get('alice', 'douyin-open', 'owner')
    assert stored.auxiliary_tokens['homepage']['access_token'] == 'home-secret'
    assert 'auxiliary_tokens' not in stored.model_dump()
    assert b'home-secret' not in store.path.read_bytes() and b'video-secret' not in store.path.read_bytes()
    with pytest.raises(ChannelError): exchange_miniapp(service, started['state'], 'code', {})
