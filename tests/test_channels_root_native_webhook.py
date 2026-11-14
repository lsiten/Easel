"""The running application accepts signed native callbacks and resolves stored receipts."""
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from easel._channel_publish_models import ChannelProgress
from easel.channel_monitor import ChannelMonitor
from easel.channels import CredentialStore
from easel.channels.completion_events import load_verified_events
from easel.channels.models import PublishReceipt
from easel.channels.native_webhooks import load_native_webhook_events
from easel.publish_ledger import PublishLedger, RunState
from web import app as web


def stored_run(ledger, persona, account_uid, *, app_client='app', external='share', native_actor=False):
    receipt = PublishReceipt(state='requires_user_action', external_request_id=external,
        details={'share_id': external, 'account_uid': account_uid, 'schema': 'snssdk1128://openplatform/share?state=' + external})
    progress = ChannelProgress(stage='publication_user_action', receipt=receipt)
    payload = {'app_client_id': app_client, 'channel_progress': progress.model_dump(mode='json')}
    if native_actor: payload.update(auth_mode='app_handoff', handoff_template='server-template')
    run = ledger.create_run(platform='douyin-open', persona=persona, account_id=account_uid,
        idempotency_key=persona + ':' + account_uid, approval_digest='approval', media_manifest_hash=persona,
        payload=payload)
    for old, new in ((RunState.PREPARED, RunState.APPROVED), (RunState.APPROVED, RunState.QUEUED), (RunState.QUEUED, RunState.RUNNING)):
        ledger.transition(run.run_id, expected=old, target=new, actor='test')
    lease = ledger.acquire_lease(run.run_id, 'test')
    ledger.checkpoint_run(run.run_id, expected=RunState.RUNNING, target=RunState.UNKNOWN,
        fencing_token=lease.fencing_token, payload={}, external_request_id=external)
    ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)
    return ledger.get_run(run.run_id)


def test_root_native_callback_before_checkpoint_resolves_only_server_owned_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setenv('EASEL_DOUYIN_OPEN_CLIENT_ID', 'app')
    monkeypatch.setenv('EASEL_DOUYIN_OPEN_CLIENT_SECRET', 'secret')
    monkeypatch.delenv('EASEL_WRITE_TOKEN', raising=False)
    remote = TestClient(web.app, base_url='http://localhost:7860', client=('203.0.113.4', 9000))
    body = json.dumps({'event': 'create_video', 'open_id': 'owner', 'content': {'share_id': 'share', 'video_id': '123'}}).encode()
    signature = hmac.new(b'secret', body, hashlib.sha256).hexdigest()
    endpoint = '/api/channels/douyin-open/webhook'
    assert remote.post(endpoint, content=body, headers={'x-douyin-signature': 'invalid'}).status_code == 403
    assert remote.post(endpoint, content=body, headers={'x-douyin-signature': signature}).status_code == 200
    store = CredentialStore(tmp_path / '_channels')
    assert len(load_native_webhook_events(store)) == 1
    assert web._process_native_app_webhooks() == 0
    ledger = PublishLedger(tmp_path / '_publish/ledger.sqlite3')
    alice = stored_run(ledger, 'alice', 'owner')
    foreign = stored_run(ledger, 'bob', 'foreign')
    wrong_app = stored_run(ledger, 'carol', 'owner', app_client='different-app')
    wrong_id = stored_run(ledger, 'dana', 'owner', external='unrelated-share')
    with store.connection() as db: db.execute('UPDATE native_webhook_inbox SET next_attempt=0')
    assert web._process_native_app_webhooks() == 1
    verified = load_verified_events(store)
    assert len(verified) == 1 and verified[0]['persona'] == 'alice' and verified[0]['account_uid'] == 'owner'
    assert remote.post(endpoint, content=body, headers={'x-douyin-signature': signature}).status_code == 200
    assert web._process_native_app_webhooks() == 0
    monitor = ChannelMonitor(ledger)
    assert monitor.consume_webhooks(tmp_path) == 1
    item = monitor.claim_readback(time.time())
    assert item['run_id'] == alice.run_id and item['work_id'] == '123'
    assert monitor.claim_readback(time.time()) is None
    assert all(ledger.get_run(run.run_id).state == RunState.UNKNOWN for run in (alice, foreign, wrong_app, wrong_id))


def test_root_native_resolver_rejects_unattested_deferred_actor(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    monkeypatch.setenv('EASEL_DOUYIN_OPEN_CLIENT_ID', 'app')
    ledger = PublishLedger(tmp_path / '_publish/ledger.sqlite3')
    stored_run(ledger, 'alice', 'app-handoff:unsafe')
    legitimate = stored_run(ledger, 'bob', 'app-handoff:verified-template', native_actor=True)
    assert web._resolve_channel_webhook_targets('douyin-open', ['share']) == [('bob', legitimate.account_id)]
    assert web._resolve_channel_webhook_targets('douyin-open', ['caller-provided-id']) == []
