"""Scheduled flow dispatch, crash recovery and scope contracts."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from easel.publish_dispatch import dispatch_one, recover_tasks
from easel.publish_ledger import PublishLedger, RunState
from easel.publish_tasks import FlowItem, FlowRequest, TaskStore


def _request():
    return FlowRequest(persona='alice', flow_id='flow-1', publish_at=datetime.now(timezone.utc)-timedelta(seconds=1),
                       content={'title': 'T', 'body': 'reviewed'}, reviewed=True,
                       items=(FlowItem(platform='twitter', account_uid='u', approval_digest='a'*64),))


def test_flow_idempotency_scope_claim_and_dispatch_once(tmp_path):
    store, ledger = TaskStore(tmp_path), PublishLedger(tmp_path / 'ledger.sqlite3')
    request = _request()
    flow = store.create(request)
    assert store.create(request)['id'] == flow['id']
    with pytest.raises(ValueError):
        store.create(request.model_copy(update={'content': {'title': 'edited'}}))
    with pytest.raises(KeyError):
        store.flow('bob', flow['id'])
    calls = []
    async def submit(platform, payload):
        calls.append((platform, payload))
        return 409, {'detail': 'approval expired'}
    assert asyncio.run(dispatch_one(store, ledger, submit))
    assert not asyncio.run(dispatch_one(store, ledger, submit))
    assert len(calls) == 1
    task = store.flow('alice', flow['id'])['tasks'][0]
    assert task['state'] == 'failed'
    assert calls[0][1]['idempotency_key'] == task['id']


def test_unknown_is_not_retried_and_dead_pre_submit_claim_recovers(tmp_path):
    store, ledger = TaskStore(tmp_path), PublishLedger(tmp_path / 'ledger.sqlite3')
    flow = store.create(_request())
    task_id, _platform, _payload = store.claim('999999999:dead', float('inf'))
    recover_tasks(store, ledger)
    assert store.task('alice', task_id)['state'] == 'queued'
    async def interrupted(platform, payload):
        raise OSError('connection lost')
    assert asyncio.run(dispatch_one(store, ledger, interrupted))
    assert store.task('alice', task_id)['state'] == 'unknown'
    with pytest.raises(ValueError):
        store.control('alice', task_id, 'retry', publish_at=datetime.now(timezone.utc),
                      approval={'reviewed': True, 'approval_digest': 'new'})


def test_recovery_projects_existing_durable_run_without_second_submit(tmp_path):
    store, ledger = TaskStore(tmp_path), PublishLedger(tmp_path / 'ledger.sqlite3')
    flow = store.create(_request())
    task_id, platform, payload = store.claim('999999999:dead', float('inf'))
    run = ledger.create_run(platform=platform, persona='alice', idempotency_key=task_id,
                            account_id='u', approval_digest='a', media_manifest_hash='m')
    ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.CANCELLED, actor='test')
    recover_tasks(store, ledger)
    restored = store.flow('alice', flow['id'])['tasks'][0]
    assert restored['state'] == 'cancelled' and restored['run_id'] == run.run_id


def test_reschedule_requires_fresh_review_and_updates_dispatch_content(tmp_path):
    store = TaskStore(tmp_path)
    flow = store.create(_request())
    task_id = flow['tasks'][0]['id']
    future = datetime.now(timezone.utc)+timedelta(days=1)
    with pytest.raises(ValueError):
        store.control('alice', task_id, 'reschedule', publish_at=future)
    store.control('alice', task_id, 'reschedule', publish_at=future,
                  approval={'reviewed': True, 'approval_digest': 'fresh'})
    assert store.claim('worker', future.timestamp()-1) is None
    claimed = store.claim('worker', future.timestamp()+1)
    assert claimed[2]['scheduled_for'] == future.isoformat()
    assert claimed[2]['approval_digest'] == 'fresh'


def test_partial_provider_stage_error_projects_durable_pending_state(tmp_path):
    store, ledger = TaskStore(tmp_path), PublishLedger(tmp_path / 'ledger.sqlite3')
    flow = store.create(_request())
    async def partial(platform, payload):
        run = ledger.create_run(platform=platform, persona=payload['persona'],
            idempotency_key=payload['idempotency_key'], account_id='u', approval_digest='a', media_manifest_hash='m')
        for old, new in ((RunState.PREPARED, RunState.APPROVED), (RunState.APPROVED, RunState.QUEUED),
                         (RunState.QUEUED, RunState.RUNNING), (RunState.RUNNING, RunState.SUBMITTED)):
            ledger.transition(run.run_id, expected=old, target=new, actor='provider')
        return 403, {'detail': 'policy changed after upload session initialized'}
    assert asyncio.run(dispatch_one(store, ledger, partial))
    task = store.flow('alice', flow['id'])['tasks'][0]
    assert task['state'] == 'submitted' and task['run_id']
    with pytest.raises(ValueError):
        store.control('alice', task['id'], 'retry', publish_at=datetime.now(timezone.utc),
                      approval={'reviewed': True, 'approval_digest': 'new'})
