"""Native malformed responses and text limits fail before losing authorization state."""
import time

import httpx
import pytest
from pydantic import SecretStr

from easel.channels import ChannelAdapter, CredentialStore, OAuthService
from easel.channels.models import ChannelError, Credential, MediaInput, PublishContent
from easel.channels.native_webhooks import verify_native
from easel.channels.douyin_open import DouyinOpen
from easel.channels.router import application_from_settings


def native_oauth(tmp_path, payload):
    config = {'EASEL_BILIBILI_OPEN_CLIENT_ID': 'app', 'EASEL_BILIBILI_OPEN_CLIENT_SECRET': 'secret',
        'EASEL_BILIBILI_OPEN_REDIRECT_URI': 'http://localhost/callback'}
    store = CredentialStore(tmp_path)
    old = Credential(persona='alice', platform='bilibili-open', account_uid='owner', access_token=SecretStr('old'), refresh_token=SecretStr('refresh'))
    store.save(old)
    def respond(request):
        assert request.url.path == '/x/account-oauth2/v1/refresh_token' and request.method == 'POST'
        return httpx.Response(200, json={'code': 0, 'data': payload})
    service = OAuthService(store, lambda provider: application_from_settings(provider, config), settings=config,
        transport=httpx.MockTransport(respond))
    return service, store, old


def test_native_numeric_string_expiry_refreshes_real_lifetime(tmp_path):
    service, store, old = native_oauth(tmp_path, {'access_token': 'new', 'expires_in': '3600', 'open_id': 'owner'})
    value = service.refresh(old)
    assert 3590 < value.expires_at - time.time() <= 3600
    assert store.get('alice', 'bilibili-open', 'owner').access_token.get_secret_value() == 'new'


@pytest.mark.parametrize('extra,code', [
    ({'open_id': 'foreign'}, 'refreshed_identity_mismatch'),
    ({'expires_in': 'NaN'}, 'token_expiry_invalid'),
    ({'expires_in': '-1'}, 'token_expiry_invalid'),
    ({'expires_in': True}, 'token_expiry_invalid'),
])
def test_invalid_refresh_preserves_original_credential(tmp_path, extra, code):
    service, store, old = native_oauth(tmp_path, {'access_token': 'new', **extra})
    with pytest.raises(ChannelError, match=code): service.refresh(old)
    assert store.get('alice', 'bilibili-open', 'owner').access_token.get_secret_value() == 'old'
    assert len(store.accounts('alice', 'bilibili-open')) == 1


@pytest.mark.parametrize('platform,content,code', [
    ('douyin-open', PublishContent(title='😀' * 16, media=(MediaInput(kind='video', url='https://media.example/video.mp4'),)), 'title_too_long'),
    ('bilibili-open', PublishContent(title='Video', body='#' + '𠀀' * 100, media=(MediaInput(kind='video', media_id='upload'),), options={'tid': 1}), 'archive_tags_invalid'),
    ('kwai-open', PublishContent(title='Video', body='#a #b #c #d #e', media=(MediaInput(kind='video', media_id='upload'),)), 'topics_too_many'),
])
def test_source_utf16_and_topic_limits_precede_remote_calls(platform, content, code):
    calls = []
    def respond(request):
        calls.append(request)
        raise AssertionError('validation must precede platform traffic')
    account = Credential(persona='alice', platform=platform, account_uid='owner', access_token=SecretStr('access'))
    adapter = ChannelAdapter(platform, transport=httpx.MockTransport(respond))
    with pytest.raises(ChannelError, match=code): adapter.publish(account, content)
    assert calls == []


def test_non_ascii_signature_is_rejected_without_error():
    config = {'EASEL_DOUYIN_OPEN_CLIENT_SECRET': 'secret'}
    assert not verify_native('douyin-open', b'{}', {'x-douyin-signature': '无效'}, {}, config)


@pytest.mark.parametrize('identity', [True, {'claimed': 'video'}, ['video']])
def test_native_share_query_rejects_non_identity_completion(identity):
    config = {'EASEL_DOUYIN_OPEN_CLIENT_ID': 'app', 'EASEL_DOUYIN_OPEN_CLIENT_SECRET': 'secret'}
    def respond(request):
        value = {'access_token': 'client', 'expires_in': 7200} if request.url.path == '/oauth/client_token/' else {'share_id': 'share', 'video_id': identity}
        return httpx.Response(200, json={'data': value})
    app = DouyinOpen(config, transport=httpx.MockTransport(respond))
    with pytest.raises(ChannelError, match='share_result_identity_invalid'):
        app.get_share_publish_result('share')
