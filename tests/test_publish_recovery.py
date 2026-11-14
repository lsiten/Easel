"""Crash and replay regressions for the publishing ledger."""
from pathlib import Path
import subprocess
import sys

import pytest

from easel.publish_ledger import IdempotencyConflict, LeaseRequired, PublishLedger, RunState
from easel.publish_recovery import recover_interrupted


def _run(ledger):
    run = ledger.create_run(platform='zhihu', persona='alice', idempotency_key='first',
                            approval_digest='reviewed', media_manifest_hash='media')
    ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.APPROVED, actor='reviewer')
    ledger.transition(run.run_id, expected=RunState.APPROVED, target=RunState.QUEUED, actor='api')
    return run


def test_run_id_returned_to_client_reuses_original_intent(tmp_path):
    ledger = PublishLedger(tmp_path / 'publish.db')
    run = _run(ledger)
    retried = ledger.create_run(platform='zhihu', persona='alice', idempotency_key=run.run_id,
                                approval_digest='reviewed', media_manifest_hash='media')
    assert retried.run_id == run.run_id
    assert len(ledger.list_runs(persona='alice')) == 1


def test_process_exit_recovers_unknown_and_fences_old_worker(tmp_path):
    database = tmp_path / 'publish.db'
    child = '''
import sys
from pathlib import Path
from easel.publish_ledger import PublishLedger, RunState
from easel.publish_recovery import worker_owner
ledger=PublishLedger(Path(sys.argv[1]))
run=ledger.create_run(platform='x',persona='alice',idempotency_key='crash',approval_digest='a',media_manifest_hash='m')
ledger.transition(run.run_id,expected=RunState.PREPARED,target=RunState.APPROVED,actor='test')
ledger.transition(run.run_id,expected=RunState.APPROVED,target=RunState.QUEUED,actor='test')
lease=ledger.acquire_lease(run.run_id,worker_owner(),ttl_seconds=900)
ledger.transition(run.run_id,expected=RunState.QUEUED,target=RunState.RUNNING,actor='test',fencing_token=lease.fencing_token)
print(run.run_id)
'''
    exited = subprocess.run([sys.executable, '-c', child, str(database)], check=True, capture_output=True, text=True)
    run_id = exited.stdout.strip()
    ledger = PublishLedger(database)
    assert recover_interrupted(ledger) == (run_id,)
    assert ledger.get_run(run_id).state == RunState.UNKNOWN
    with pytest.raises(LeaseRequired):
        ledger.transition(run_id, expected=RunState.UNKNOWN, target=RunState.PUBLISHED,
                          actor='stale', fencing_token=1)


def test_publication_state_and_outbox_commit_together(tmp_path):
    ledger = PublishLedger(tmp_path / 'publish.db')
    run = _run(ledger)
    ledger.transition(run.run_id, expected=RunState.QUEUED, target=RunState.RUNNING, actor='worker')
    ledger.transition(run.run_id, expected=RunState.RUNNING, target=RunState.PUBLISHED, actor='worker')
    reopened = PublishLedger(tmp_path / 'publish.db')
    pending = reopened.pending_effects('publish_projection')
    assert len(pending) == 1 and pending[0].run_id == run.run_id
    with pytest.raises(IdempotencyConflict):
        reopened.record_effect(run.run_id, effect_key='different', effect_type='publish_projection')
