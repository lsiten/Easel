"""Signed notifications wake known stages and queue owned exact-ID evidence reads."""
import asyncio
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
from pydantic import SecretStr

from easel.channel_monitor import ChannelMonitor
from easel.channels import Credential, CredentialStore
from easel.channels.completion_events import load_verified_events
from easel.publish_ledger import PublishLedger, RunState
from web import app as web
from tests.test_channel_publishing import Harness, harness, intent


def _run(ledger, persona='alice', external='pub-1'):
    run = ledger.create_run(platform='tiktok', persona=persona, account_id='actor', idempotency_key=persona,
                            approval_digest='a', media_manifest_hash='m', payload={'title': 'T'})
    for old, new in ((RunState.PREPARED, RunState.APPROVED), (RunState.APPROVED, RunState.QUEUED), (RunState.QUEUED, RunState.RUNNING)):
        ledger.transition(run.run_id, expected=old, target=new, actor='test')
    lease = ledger.acquire_lease(run.run_id, 'test-worker')
    ledger.checkpoint_run(run.run_id, expected=RunState.RUNNING, target=RunState.UNKNOWN,
                         fencing_token=lease.fencing_token, external_request_id=external, payload={})
    ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)
    return run


def test_signed_matching_hook_queues_readback_without_trusting_success(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setenv('EASEL_TIKTOK_WEBHOOK_SECRET', 'offline')
    store = CredentialStore(tmp_path / '_channels')
    for persona in ('alice', 'bob'):
        store.save(Credential(persona=persona, platform='tiktok', account_uid='actor', access_token=SecretStr('token')))
    ledger = PublishLedger(tmp_path / '_publish/ledger.sqlite3')
    alice = _run(ledger)
    bob = _run(ledger, 'bob', 'other-publish-request')
    body = json.dumps({'event': 'post.publish.complete', 'user_openid': 'actor',
                       'content': json.dumps({'publish_id': 'pub-1', 'post_id': 'work-1', 'status': 'complete'})}).encode()
    stamp = str(int(time.time()))
    signature = 't=' + stamp + ',s=' + hmac.new(b'offline', stamp.encode()+b'.'+body, hashlib.sha256).hexdigest()
    remote = TestClient(web.app, base_url='http://localhost:7860', client=('203.0.113.4', 9000))
    assert remote.post('/api/channels/tiktok/webhook', content=body, headers={'TikTok-Signature': signature}).status_code == 202
    monitor = ChannelMonitor(ledger)
    assert monitor.consume_webhooks(tmp_path) == 2
    assert load_verified_events(store) == []
    item = monitor.claim_readback(time.time())
    assert item['run_id'] == alice.run_id and item['work_id'] == 'work-1'
    assert json.loads(item['scope_json'])['persona'] == 'alice'
    assert monitor.claim_readback(time.time()) is None
    assert ledger.get_run(alice.run_id).state == RunState.UNKNOWN
    assert ledger.get_run(bob.run_id).state == RunState.UNKNOWN
    monitor.finish_readback(item, False)
    assert monitor.claim_readback(time.time()) is None
    assert monitor.consume_webhooks(tmp_path) == 0


def test_monitor_completes_original_pending_container_once(harness: Harness):
    from easel.channel_monitor import monitor_one
    from easel.publish_recovery import recover_interrupted
    payload = harness.approve('instagram', intent('project/image.jpg'))
    pending = harness.client.post('/api/channel-publish/instagram/publish', json=payload).json()
    assert pending['state'] == 'submitted'
    ledger = PublishLedger(harness.root / '_publish/ledger.sqlite3')
    assert recover_interrupted(ledger) == ()
    harness.provider.ready = True
    async def finalize(platform, run_id, scope):
        response = harness.client.post(f'/api/channel-publish/{platform}/{run_id}/finalize', json=scope)
        return response.status_code, response.json()
    assert asyncio.run(monitor_one(ledger, harness.root, finalize))
    assert ledger.get_run(pending['run_id']).state == RunState.PUBLISHED
    assert not asyncio.run(monitor_one(ledger, harness.root, finalize))
    assert len(harness.provider.posts) == 1
    assert len(harness.provider.containers) == 1
