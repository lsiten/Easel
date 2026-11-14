"""Root task dispatch recovers frozen reviewed plans after credential replacement."""
from __future__ import annotations

import anyio
from datetime import datetime, timedelta, timezone
import json
import time

from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest

from easel.channels import Credential
from easel.channels.http import ProviderHttp
from easel.publish_dispatch import dispatch_one
from easel.publish_ledger import PublishLedger
from easel.publish_tasks import TaskStore
from tests.test_channel_publishing import Harness, harness, intent


@pytest.mark.parametrize('interrupted', [False, True])
def test_root_review_restores_same_failed_task_with_deleted_sources_and_expired_initial_review(
        harness: Harness, monkeypatch: pytest.MonkeyPatch, interrupted: bool):
    from web import app as web
    import easel._channel_reapproval as reapproval
    monkeypatch.setattr(web, 'OUTPUTS_DIR', harness.root)
    monkeypatch.setattr(web, 'profile_exists', lambda persona: persona in {'alice', 'bob'})
    monkeypatch.setattr(web, 'read_settings', lambda path: harness.settings)
    original_init = ProviderHttp.__init__
    def provider_init(self, platform, *, transport=None):
        original_init(self, platform, transport=httpx.MockTransport(harness.provider.handle))
    monkeypatch.setattr(ProviderHttp, '__init__', provider_init)
    client = TestClient(web.app, base_url='http://localhost:7860', headers={'Origin': 'http://localhost:7860'})
    stamp = datetime.now(timezone.utc) + timedelta(hours=1)
    content = intent('project/video.mp4')
    approval = client.post('/api/channel-publish/youtube/precheck', json={**content, 'scheduled_for': stamp.isoformat()}).json()
    flow = client.post('/api/publish-flows', json={'persona': 'alice', 'flow_id': 'credential-rotation',
        'publish_at': stamp.isoformat(), 'content': content, 'reviewed': True,
        'items': [{'platform': 'youtube', 'account_uid': 'actor', **{key: approval[key]
            for key in ('approval_digest', 'approval_expires_at')}}]})
    assert flow.status_code == 200, flow.text
    task = flow.json()['tasks'][0]
    run_id, task_id = task['run_id'], task['id']
    store, ledger = TaskStore(harness.root / '_publish'), PublishLedger(harness.root / '_publish/ledger.sqlite3')
    (harness.root / 'project/video.mp4').unlink()
    harness.store.save(Credential(persona='alice', platform='youtube', account_uid='actor', access_token=SecretStr('new-token')))
    monkeypatch.setattr(time, 'time', lambda: stamp.timestamp() + 1900)
    assert anyio.run(dispatch_one, store, ledger, web._submit_scheduled_publish)
    assert harness.provider.posts == harness.provider.uploads == []
    # A stored failure from an older dispatcher version must resume this same queued run.
    with store.connect() as db:
        db.execute("UPDATE tasks SET state='failed' WHERE id=?", (task_id,))
        db.execute("INSERT INTO tasks(id,flow_id,persona,platform,account_uid,publish_at,state,payload_json) VALUES(?,?,?,?,?,?,?,?)",
            ('unrelated', task['flow_id'], 'bob', 'youtube', 'actor', stamp.timestamp(), 'failed',
             json.dumps({'idempotency_key': task_id, 'approval_digest': approval['approval_digest']})))
    endpoint = f'/api/channel-publish/youtube/{run_id}'
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    fresh = client.post(endpoint + '/reapprove/precheck', json=scope)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()['approval_expires_at'] > approval['approval_expires_at']
    reviewed = {**scope, 'reviewed': True, **{key: fresh.json()[key] for key in ('approval_digest', 'approval_expires_at')}}
    restore = reapproval.restore_reviewed_task
    if interrupted:
        def crash(root, run):
            raise OSError('interrupted after durable approval')
        monkeypatch.setattr(reapproval, 'restore_reviewed_task', crash)
        with pytest.raises(OSError, match='interrupted after durable approval'):
            client.post(endpoint + '/reapprove', json=reviewed)
        assert store.task('alice', task_id)['state'] == 'failed'
        monkeypatch.setattr(reapproval, 'restore_reviewed_task', restore)
    result = client.post(endpoint + '/reapprove', json=reviewed)
    assert result.status_code == 200 and result.json()['state'] == 'queued', result.text
    task_after = store.task('alice', task_id)
    assert task_after['state'] == 'queued' and task_after['run_id'] == run_id
    assert task_after['publish_at'] == task['publish_at']
    assert store.task('bob', 'unrelated')['state'] == 'failed'
    assert harness.provider.posts == harness.provider.uploads == []
    assert anyio.run(dispatch_one, store, ledger, web._submit_scheduled_publish)
    assert not anyio.run(dispatch_one, store, ledger, web._submit_scheduled_publish)
    assert store.task('alice', task_id)['state'] == 'published'
    assert len(ledger.list_runs(persona='alice')) == len(harness.provider.posts) == 1
    assert harness.provider.uploads == [b'approved-video']
