"""OAuth concurrency and durable, scoped webhook delivery boundaries."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from easel.channels import ChannelError, Credential, CredentialStore, OAuthApplication, OAuthService
from easel.channels.completion_events import ack_verified_event, defer_verified_event, load_verified_events
from easel.channels.router import create_router


def test_first_open_concurrency_and_rotating_refresh_are_serialized(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        stores = list(pool.map(lambda _: CredentialStore(tmp_path), range(16)))
    store = stores[0]
    old = Credential(persona='alice', platform='twitter', account_uid='actor', access_token=SecretStr('old'), refresh_token=SecretStr('refresh'))
    store.save(old)
    requests = []
    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'rotated'})
    application = OAuthApplication(client_id='client', client_secret=SecretStr('secret'), redirect_uri='http://localhost/callback')
    oauth = OAuthService(store, lambda _: application, transport=httpx.MockTransport(provider))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: oauth.refresh(old), range(2)))
    assert len(requests) == 1
    assert all(result.refresh_token.get_secret_value() == 'rotated' for result in results)


def test_facebook_page_access_refresh_uses_authenticated_parent_user(tmp_path):
    store = CredentialStore(tmp_path)
    parent = Credential(persona='alice', platform='facebook', account_uid='user', access_token=SecretStr('user-token'))
    page = Credential(persona='alice', platform='facebook', account_uid='page', parent_uid='user', access_token=SecretStr('stale-page-token'))
    store.save(parent)
    store.save(page)
    def provider(request):
        assert request.url.path.endswith('/me/accounts')
        assert request.headers['authorization'] == 'Bearer user-token'
        return httpx.Response(200, json={'data': [{'id': 'page', 'name': 'Page', 'access_token': 'new-page-token'}]})
    application = OAuthApplication(client_id='client', client_secret=SecretStr('secret'), redirect_uri='http://localhost/callback')
    value = OAuthService(store, lambda _: application, transport=httpx.MockTransport(provider)).refresh(page)
    assert value.access_token.get_secret_value() == 'new-page-token' and value.parent_uid == 'user'


def test_oauth_provider_failure_marks_session_failed_and_consumes_state(tmp_path):
    store = CredentialStore(tmp_path)
    application = OAuthApplication(client_id='client', client_secret=SecretStr('secret'), redirect_uri='http://localhost/callback')
    oauth = OAuthService(store, lambda _: application, transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    state = oauth.start('alice', 'twitter')['state']
    with pytest.raises(ChannelError, match='provider_http_error'): oauth.exchange('twitter', 'code', state)
    assert store.session_status('alice', 'twitter', state)['status'] == 'failed'
    with pytest.raises(ChannelError, match='oauth_state_invalid'): oauth.exchange('twitter', 'code', state)


def test_post_form_oauth_denial_is_single_use_and_pollable(tmp_path):
    app = FastAPI()
    settings = {'EASEL_TWITTER_CLIENT_ID': 'client', 'EASEL_TWITTER_REDIRECT_URI': 'http://localhost/callback'}
    app.include_router(create_router(lambda: tmp_path, lambda: settings, lambda name: name == 'alice', lambda request: None))
    with TestClient(app) as client:
        started = client.post('/api/channels/twitter/auth/start', json={'persona': 'alice'}).json()
        denied = client.post('/api/channels/twitter/auth/callback', data={'state': started['state'], 'error': 'access_denied'})
        assert denied.status_code == 400 and denied.json()['detail']['code'] == 'oauth_authorization_denied'
        status = client.get('/api/channels/twitter/auth/status', params={'persona': 'alice', 'state': started['state']})
        assert status.json()['status'] == 'failed'
        replay = client.post('/api/channels/twitter/auth/callback', json={'state': started['state'], 'error': 'access_denied'})
        assert replay.status_code == 403


def test_webhook_oldest_backlog_ack_scope_backoff_and_quarantine(tmp_path):
    store = CredentialStore(tmp_path / '_channels')
    store.save(Credential(persona='alice', platform='facebook', account_uid='actor', access_token=SecretStr('token')))
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, lambda: {'EASEL_FACEBOOK_WEBHOOK_SECRET': 'secret'}, lambda name: name == 'alice', lambda request: None))
    def deliver(client, number):
        body = json.dumps({'object': 'page', 'entry': [{'id': 'actor', 'changes': [{'field': 'feed', 'value': {'post_id': str(number)}}]}]}).encode()
        signature = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
        response = client.post('/api/channels/facebook/webhook', content=body, headers={'X-Hub-Signature-256': signature})
        assert response.status_code == 202 and response.json()['queued_events'] == 1
    def key(event): return event['event_id'], event['persona'], event['platform'], event['account_uid']
    with TestClient(app) as client:
        for index in range(121): deliver(client, index)
        first = load_verified_events(store)
        assert len(first) == 100 and first[0]['events'][0]['platform_work_id'] == '0'
        assert not ack_verified_event(store, first[0]['event_id'], 'bob', 'facebook', 'actor')
        for event in first: assert ack_verified_event(store, *key(event))
        remaining = load_verified_events(store)
        assert len(remaining) == 21 and remaining[0]['events'][0]['platform_work_id'] == '100'
        assert defer_verified_event(store, *key(remaining[0]), retry_after=60, error_code='not_visible_yet')
        due = load_verified_events(store)
        assert len(due) == 20 and due[0]['events'][0]['platform_work_id'] == '101'
        for event in due: ack_verified_event(store, *key(event))
        assert load_verified_events(store) == []
        assert len(load_verified_events(store, now=time.time() + 61)) == 1
        ack_verified_event(store, *key(remaining[0]))
        defer_verified_event(store, *key(remaining[0]), retry_after=0)
        assert load_verified_events(store) == []
        deliver(client, 999)
        deliver(client, 1000)
        corrupt = load_verified_events(store)[0]
        with store.connection() as db:
            db.execute('UPDATE webhook_events SET encrypted=? WHERE digest=?', (b'corrupt', corrupt['event_id']))
        valid = load_verified_events(store)
        assert len(valid) == 1 and valid[0]['events'][0]['platform_work_id'] == '1000'
        with store.connection() as db:
            row = db.execute('SELECT processed,error_code FROM webhook_deliveries WHERE digest=?', (corrupt['event_id'],)).fetchone()
        assert row['processed'] == 1 and row['error_code'] == 'inbox_integrity_failed'


def test_multi_account_signed_webhook_payloads_remain_persona_scoped(tmp_path):
    store = CredentialStore(tmp_path / '_channels')
    for persona in ('alice', 'bob'):
        store.save(Credential(persona=persona, platform='facebook', account_uid=persona, access_token=SecretStr('token')))
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, lambda: {'EASEL_FACEBOOK_WEBHOOK_SECRET': 'secret'}, lambda _: True, lambda request: None))
    body = json.dumps({'object': 'page', 'entry': [{'id': name, 'changes': [{'field': 'feed', 'value': {'post_id': name + '-work'}}]} for name in ('alice', 'bob')]}).encode()
    signature = 'sha256=' + hmac.new(b'secret', body, hashlib.sha256).hexdigest()
    with TestClient(app) as client:
        assert client.post('/api/channels/facebook/webhook', content=body, headers={'X-Hub-Signature-256': signature}).json()['queued_events'] == 2
        events = client.get('/api/channels/facebook/webhook-events', params={'persona': 'alice', 'account_uid': 'alice'})
        assert 'alice-work' in events.text and 'bob-work' not in events.text
