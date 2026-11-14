"""Actor changes and uncertain intent replay at normal publish boundaries."""
from fastapi.testclient import TestClient

from easel.publish_ledger import IdempotencyConflict, PublishLedger, RunState
from web import app as web
import pytest


def test_current_browser_actor_must_match_reviewed_uid(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'OUTPUTS_DIR', tmp_path)
    env = tmp_path / '.env'
    env.write_text('')
    monkeypatch.setattr(web, 'ENV_FILE', env)
    monkeypatch.setattr(web, '_publish_account_uid', lambda platform, persona: 'reviewed-actor')
    async def current_actor(platform, persona, force=False):
        assert force is True
        return {'loggedIn': True, 'accountUid': 'other-actor'}
    monkeypatch.setattr(web, '_check_account_whoami', current_actor)
    def forbidden(*args, **kwargs):
        pytest.fail('Actor mismatch must stop before external publish')
    monkeypatch.setattr(web.subprocess, 'run', forbidden)
    client = TestClient(web.app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
    intent = {'title': 'T', 'body': 'B', 'reviewed': True}
    precheck = client.post('/api/publish/zhihu/precheck', json=intent)
    assert precheck.json()['accountUid'] == 'reviewed-actor'
    intent['approval_digest'] = precheck.json()['approvalDigest']
    assert client.post('/api/publish/zhihu', json=intent).status_code == 409
    assert not (tmp_path / '_publish/ledger.sqlite3').exists()


def test_uncertain_intent_cannot_bypass_with_new_key_or_schedule(tmp_path):
    ledger = PublishLedger(tmp_path / 'ledger.db')
    payload = {'channel_intent': {'title': 'T', 'body': 'B', 'scheduled_for': 'old'}}
    args = dict(platform='twitter', persona='alice', account_id='uid', approval_digest='first', media_manifest_hash='media', payload=payload)
    run = ledger.create_run(idempotency_key='first', **args)
    for old, new in ((RunState.PREPARED, RunState.APPROVED), (RunState.APPROVED, RunState.QUEUED),
                     (RunState.QUEUED, RunState.RUNNING), (RunState.RUNNING, RunState.UNKNOWN)):
        ledger.transition(run.run_id, expected=old, target=new, actor='test')
    args['approval_digest'] = 'new-review'
    args['payload'] = {'channel_intent': {'title': 'T', 'body': 'B', 'scheduled_for': 'new'}}
    with pytest.raises(IdempotencyConflict):
        ledger.create_run(idempotency_key='new-key', **args)
    assert len(ledger.list_runs(persona='alice')) == 1
