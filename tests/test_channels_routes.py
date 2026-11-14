"""Natural API boundaries for reviews, account groups, and webhook delivery."""
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from easel.channels import ChannelAdapter, Credential, CredentialStore
from easel.channels.router import create_router


def make_client(tmp_path, settings=None):
    app = FastAPI()
    app.include_router(create_router(lambda: tmp_path, lambda: settings or {}, lambda name: name in {'alice', 'bob'}, lambda request: None))
    store = CredentialStore(tmp_path / '_channels')
    for platform in ('twitter', 'facebook'):
        store.save(Credential(persona='alice', platform=platform, account_uid='own-account', access_token=SecretStr('private-token')))
    return TestClient(app)


def test_mutation_route_requires_review_and_scoped_account(tmp_path, monkeypatch):
    calls = []
    def provider(request):
        calls.append((request.method, request.url.path))
        assert request.headers['authorization'] == 'Bearer private-token'
        if request.method == 'GET': return httpx.Response(200, json={'data': {'id': 'own-work', 'author_id': 'own-account'}})
        return httpx.Response(200, json={'data': {'deleted': True}})
    original = ChannelAdapter.__init__
    def initialize(self, platform, **kwargs): original(self, platform, transport=httpx.MockTransport(provider))
    monkeypatch.setattr(ChannelAdapter, '__init__', initialize)
    with make_client(tmp_path) as client:
        payload = {'persona': 'alice', 'account_uid': 'own-account'}
        unreviewed = client.request('DELETE', '/api/channels/twitter/works/own-work', json=payload)
        assert unreviewed.status_code == 409 and not calls
        wrong_persona = client.request('DELETE', '/api/channels/twitter/works/own-work', json={**payload, 'persona': 'bob', 'reviewed': True})
        assert wrong_persona.status_code == 404 and not calls
        result = client.request('DELETE', '/api/channels/twitter/works/own-work', json={**payload, 'reviewed': True})
        assert result.status_code == 200 and result.json()['deleted'] is True
        assert calls[-1] == ('DELETE', '/2/tweets/own-work')


def test_group_crud_scope_default_reassignment_and_atomic_rank(tmp_path):
    with make_client(tmp_path) as client:
        initial = client.get('/api/channels/account-groups', params={'persona': 'alice'}).json()
        assert len(initial) == 1 and len(initial[0]['accounts']) == 2
        created = client.post('/api/channels/account-groups', json={'persona': 'alice', 'data': {'name': 'Team', 'rank': 3}})
        assert created.status_code == 200
        group_id = created.json()['id']
        assigned = client.patch(f'/api/channels/account-groups/{group_id}/accounts', json={'persona': 'alice', 'platform': 'twitter', 'account_uid': 'own-account'})
        assert assigned.status_code == 200
        rank = client.patch(f'/api/channels/account-groups/{group_id}/accounts/rank', json={'persona': 'alice', 'items': [
            {'platform': 'twitter', 'account_uid': 'own-account', 'rank': 7},
            {'platform': 'facebook', 'account_uid': 'own-account', 'rank': 8}]})
        assert rank.status_code == 404
        groups = client.get('/api/channels/account-groups', params={'persona': 'alice'}).json()
        team = next(group for group in groups if group['id'] == group_id)
        assert team['accounts'][0]['rank'] == 0
        forbidden = client.patch(f'/api/channels/account-groups/{group_id}', json={'persona': 'bob', 'data': {'name': 'stolen'}})
        assert forbidden.status_code == 404
        removed = client.request('DELETE', '/api/channels/account-groups', json={'persona': 'alice', 'ids': [group_id]})
        assert removed.status_code == 200
        groups = client.get('/api/channels/account-groups', params={'persona': 'alice'}).json()
        assert len(groups) == 1 and len(groups[0]['accounts']) == 2
        denied = client.request('DELETE', '/api/channels/account-groups', json={'persona': 'alice', 'ids': ['default']})
        assert denied.status_code == 400


def test_signed_webhook_delivery_is_scoped_encrypted_and_idempotent(tmp_path):
    body = json.dumps({'object': 'page', 'entry': [{'id': 'own-account', 'changes': [{'field': 'feed', 'value': {'post_id': 'work-1'}}]}]}).encode()
    signature = 'sha256=' + hmac.new(b'webhook-secret', body, hashlib.sha256).hexdigest()
    with make_client(tmp_path, {'EASEL_FACEBOOK_WEBHOOK_SECRET': 'webhook-secret'}) as client:
        rejected = client.post('/api/channels/facebook/webhook', content=body)
        assert rejected.status_code == 403
        accepted = client.post('/api/channels/facebook/webhook', content=body, headers={'X-Hub-Signature-256': signature})
        assert accepted.status_code == 202 and accepted.json()['queued_events'] == 1
        duplicate = client.post('/api/channels/facebook/webhook', content=body, headers={'X-Hub-Signature-256': signature})
        assert duplicate.json()['queued_events'] == 0
        events = client.get('/api/channels/facebook/webhook-events', params={'persona': 'alice', 'account_uid': 'own-account'})
        assert len(events.json()) == 1 and events.json()[0]['payload']['entry'][0]['id'] == 'own-account'
        forbidden = client.get('/api/channels/facebook/webhook-events', params={'persona': 'bob', 'account_uid': 'own-account'})
        assert forbidden.status_code == 404
        assert b'work-1' not in (tmp_path / '_channels/channels.sqlite3').read_bytes()


def test_official_analytics_route_persists_account_uid_and_missing_metrics(tmp_path, monkeypatch):
    from easel.analytics_snapshots import load_snapshots
    def provider(request):
        assert request.url.path == '/2/users/me'
        return httpx.Response(200, json={'data': {'id': 'own-account', 'public_metrics': {'followers_count': 42, 'tweet_count': 3}}})
    original = ChannelAdapter.__init__
    def initialize(self, platform, **kwargs): original(self, platform, transport=httpx.MockTransport(provider))
    monkeypatch.setattr(ChannelAdapter, '__init__', initialize)
    with make_client(tmp_path) as client:
        response = client.get('/api/channels/twitter/analytics', params={'persona': 'alice', 'account_uid': 'own-account'})
        assert response.status_code == 200
        assert response.json()['account_metrics']['followers'] == 42
        rows = load_snapshots(tmp_path, 'alice', 'twitter')
        assert len(rows) == 1 and rows[0].account_uid == 'own-account'
        assert dict(rows[0].account_metrics)['followers'] == 42
        assert dict(rows[0].account_metrics)['likes'] is None


def test_bulk_account_delete_is_scoped_and_all_or_nothing(tmp_path):
    with make_client(tmp_path) as client:
        status = client.get('/api/channels/twitter/accounts/own-account/auth-status', params={'persona': 'alice'})
        assert status.status_code == 200 and status.json()['status'] == 'connected'
        failed = client.request('DELETE', '/api/channels/twitter/accounts', json={'persona': 'alice', 'account_uids': ['own-account', 'foreign']})
        assert failed.status_code == 404
        assert len(client.get('/api/channels/twitter/accounts', params={'persona': 'alice'}).json()) == 1
        removed = client.request('DELETE', '/api/channels/twitter/accounts', json={'persona': 'alice', 'account_uids': ['own-account']})
        assert removed.status_code == 200
        assert client.get('/api/channels/twitter/accounts', params={'persona': 'alice'}).json() == []
