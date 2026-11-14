"""Native WeChat publisher identity and article checkpoint wire contracts."""
import json

import httpx
import pytest
from pydantic import SecretStr

from easel.channels import ChannelAdapter, CredentialStore, OAuthService
from easel.channels.models import AccountProfile, ChannelError, Credential, PublishContent, PublishReceipt
from easel.channels.router import application_from_settings


def config(platform):
    prefix = 'EASEL_' + platform.upper().replace('-', '_') + '_'
    return {prefix + 'CLIENT_ID': 'official-app', prefix + 'CLIENT_SECRET': 'secret', prefix + 'REDIRECT_URI': 'http://localhost/callback'}


def test_official_oauth_binds_actual_app_publisher_then_stages_article(tmp_path):
    cover = tmp_path / 'cover.jpg'
    cover.write_bytes(b'image')
    calls, submitted = [], [False]
    def respond(request):
        path = request.url.path
        calls.append(path)
        if path == '/sns/oauth2/access_token': value = {'access_token': 'oauth-access', 'refresh_token': 'refresh', 'openid': 'visitor', 'expires_in': 3600}
        elif path == '/sns/userinfo':
            assert request.url.params['openid'] == 'visitor'
            value = {'openid': 'visitor', 'nickname': 'Visitor'}
        elif path == '/cgi-bin/token':
            assert request.url.params['appid'] == 'official-app'
            value = {'access_token': 'app-token', 'expires_in': 3600}
        elif path == '/cgi-bin/material/add_material':
            assert request.url.params['access_token'] == 'app-token' and b'image' in request.content
            value = {'media_id': 'thumbnail'}
        elif path == '/cgi-bin/draft/add':
            article = json.loads(request.content)['articles'][0]
            assert article['thumb_media_id'] == 'thumbnail' and article['author'] == 'Author'
            assert article['open_comment'] == 1 and article['content'] == '<p>Article</p>'
            value = {'media_id': 'draft'}
        elif path == '/cgi-bin/freepublish/submit':
            assert json.loads(request.content) == {'media_id': 'draft'}
            submitted[0] = True
            value = {'publish_id': 'publish'}
        elif path == '/cgi-bin/freepublish/get':
            assert submitted[0] and json.loads(request.content) == {'publish_id': 'publish'}
            value = {'publish_status': 0, 'article_id': 'article', 'article_detail': {'item': [{'article_url': 'https://mp.weixin.qq.com/s/article'}]}}
        else: raise AssertionError(request.url)
        return httpx.Response(200, json=value)
    settings = config('wechat-official-open')
    store = CredentialStore(tmp_path / 'store')
    transport = httpx.MockTransport(respond)
    oauth = OAuthService(store, lambda platform: application_from_settings(platform, settings), settings=settings, transport=transport)
    started = oauth.start('alice', 'wechat-official-open')
    assert started['authorization_url'].endswith('#wechat_redirect')
    account = oauth.exchange('wechat-official-open', 'code', started['state'])
    assert account.account_uid == 'appid:official-app' and account.parent_uid == 'visitor'
    adapter = ChannelAdapter('wechat-official-open', settings=settings, transport=transport, store=store)
    draft = adapter.publish(account, PublishContent(title='Article', body='<p>Article</p>', options={'author': 'Author', 'open_comment': 1}), cover_path=cover)
    assert draft.state == 'requires_finalize' and draft.external_request_id == 'draft' and not submitted[0]
    pending = adapter.finalize(account, draft)
    assert pending.state == 'pending' and pending.external_request_id == 'publish'
    done = adapter.finalize(account, pending)
    assert done.state == 'published' and done.platform_work_id == 'article'
    assert calls.count('/cgi-bin/freepublish/submit') == 1
    other_config = {**settings, 'EASEL_WECHAT_OFFICIAL_OPEN_CLIENT_ID': 'different-app'}
    with pytest.raises(ChannelError, match='application_owner_mismatch'):
        ChannelAdapter('wechat-official-open', settings=other_config, transport=transport).publish(account, PublishContent(body='text'), cover_path=cover)


def test_channels_cookie_identity_and_actual_work_owner(tmp_path):
    def respond(request):
        if request.url.host == 'channels.weixin.qq.com':
            assert request.headers['Cookie'] == 'sessionid=secret; wxuin=123'
            assert request.headers['X-WECHAT-UIN'] == '123'
            assert json.loads(request.content)['scene'] == 7
            value = {'errCode': 0, 'data': {'finderUser': {'uniqId': 'owner', 'finderUsername': 'finder', 'nickname': 'Creator'}}}
        elif request.url.path == '/cgi-bin/token': value = {'access_token': 'app', 'expires_in': 3600}
        elif request.url.path == '/channels/ec/finder/video/get':
            assert json.loads(request.content) == {'finder_id': 'finder', 'video_id': 'video'}
            value = {'errcode': 0, 'finder_id': 'finder', 'video_id': 'video', 'description': 'Video'}
        else: raise AssertionError(request.url)
        return httpx.Response(200, json=value)
    adapter = ChannelAdapter('wechat-channels-open', settings=config('wechat-channels-open'), transport=httpx.MockTransport(respond))
    pending = Credential(persona='alice', platform='wechat-channels-open', account_uid='pending', access_token=SecretStr('sessionid=secret; wxuin=123'))
    profile = adapter.profile(pending)
    assert profile.account_uid == 'owner'
    account = pending.model_copy(update={'account_uid': 'owner', 'profile': profile})
    assert adapter.work(account, 'finder:video')['id'] == 'finder:video'
    with pytest.raises(ChannelError, match='work_owner_mismatch'): adapter.work(account, 'foreign:video')


def test_official_missing_status_stays_uncertain_instead_of_authorizing_republish():
    def respond(request):
        if request.url.path == '/cgi-bin/token': return httpx.Response(200, json={'access_token': 'app', 'expires_in': 3600})
        return httpx.Response(200, json={})
    account = Credential(persona='alice', platform='wechat-official-open', account_uid='appid:official-app', access_token=SecretStr('oauth'),
        profile=AccountProfile(account_uid='appid:official-app', extra={'client_id': 'official-app'}))
    adapter = ChannelAdapter('wechat-official-open', settings=config('wechat-official-open'), transport=httpx.MockTransport(respond))
    with pytest.raises(ChannelError, match='publication_status_invalid'):
        adapter.finalize(account, PublishReceipt(state='pending', external_request_id='publish', details={'stage': 'article_publish'}))
