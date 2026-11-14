"""Exercise migrated entry points through the production app and middleware."""
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from easel.channels import Credential, CredentialStore
from easel.publish_models import RunState
from easel.publish_projection import drain_publish_projections
from web import app as web


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setattr(web, 'profile_exists', lambda name: name in {'alice', 'bob'})
    local = TestClient(web.app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
    remote = TestClient(web.app, base_url='http://localhost:7860', client=('203.0.113.9', 3000))
    return tmp_path, local, remote


def test_external_signed_webhook_crosses_app_guard_and_dedupes(clients, monkeypatch):
    root, local, remote = clients
    monkeypatch.setenv('EASEL_FACEBOOK_WEBHOOK_SECRET', 'offline-secret')
    CredentialStore(root / '_channels').save(Credential(persona='alice', platform='facebook', account_uid='page', access_token=SecretStr('private')))
    body = json.dumps({'entry': [{'id': 'page', 'changes': []}]}).encode()
    signature = 'sha256=' + hmac.new(b'offline-secret', body, hashlib.sha256).hexdigest()
    assert remote.post('/api/channels/facebook/webhook', content=body).status_code == 403
    response = remote.post('/api/channels/facebook/webhook', content=body, headers={'X-Hub-Signature-256': signature})
    assert response.status_code == 202 and response.json()['queued_events'] == 1
    assert remote.post('/api/channels/facebook/webhook', content=body, headers={'X-Hub-Signature-256': signature}).json()['queued_events'] == 0


def test_signed_upload_completes_into_normal_chat_attachment(clients):
    root, local, remote = clients
    data = b'offline image'
    grant = local.post('/api/asset-uploads/sessions', json={'persona': 'alice', 'session_id': 'chat-1',
        'filename': 'photo.png', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'public': True}).json()
    assert remote.put(grant['upload_url'], content=data).status_code == 200
    confirmed = remote.post(grant['confirm_url'])
    assert confirmed.status_code == 200, confirmed.text
    attachment = confirmed.json()
    assert (root / attachment['path']).read_bytes() == data
    scope = web._attachment_scope('chat-1', 'alice')
    assert attachment['id'] == web._attachment_id(scope, attachment['path'])
    assert remote.post(grant['confirm_url']).json() == attachment
    assert local.get('/api/asset-uploads/' + grant['id'], params={'persona': 'bob'}).status_code == 404


def test_record_scope_public_redaction_and_terminal_tombstone(clients):
    root, local, remote = clients
    ledger = web._publish_ledger()
    run = ledger.create_run(platform='twitter', persona='alice', account_id='actor', idempotency_key='record',
        approval_digest='a', media_manifest_hash='m', payload={'title': 'T', 'body': 'private body', 'token': 'secret'})
    for old, new in ((RunState.PREPARED, RunState.APPROVED), (RunState.APPROVED, RunState.QUEUED),
                     (RunState.QUEUED, RunState.RUNNING), (RunState.RUNNING, RunState.PUBLISHED)):
        ledger.transition(run.run_id, expected=old, target=new, actor='test')
    public = remote.get(f'/api/publish-records/public/{run.run_id}').json()
    assert not {'persona', 'account_uid', 'title', 'body', 'token'}.intersection(public)
    assert local.get(f'/api/publish-records/{run.run_id}?persona=bob').status_code == 404
    assert local.get('/api/publish-records/published?persona=alice').json()['total'] == 1
    assert local.delete(f'/api/publish-records/{run.run_id}?persona=alice').status_code == 409
    drain_publish_projections(ledger, root)
    assert local.delete(f'/api/publish-records/{run.run_id}?persona=alice').status_code == 200
    assert local.get('/api/publish-records?persona=alice').json()['total'] == 0
    assert remote.get(f'/api/publish-records/public/{run.run_id}').status_code == 404
    assert ledger.get_run(run.run_id) is not None
