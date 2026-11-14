from pathlib import Path

import pytest

from easel.publish_ledger import (
    IdempotencyConflict,
    InvalidTransition,
    LeaseBusy,
    LeaseRequired,
    PublishLedger,
    RunState,
    digest_mapping,
)


def make_ledger(tmp_path: Path) -> PublishLedger:
    return PublishLedger(tmp_path / "ledger.sqlite3")


def test_create_is_idempotent_and_rejects_changed_approval(tmp_path):
    ledger = make_ledger(tmp_path)
    kwargs = dict(
        platform="xiaohongshu", persona="alice", idempotency_key="draft-1",
        approval_digest="a", media_manifest_hash="m", payload={"title": "T"},
    )
    first = ledger.create_run(**kwargs)
    second = ledger.create_run(**kwargs)
    assert first.run_id == second.run_id
    assert len(ledger.list_events(first.run_id)) == 1
    with pytest.raises(IdempotencyConflict):
        ledger.create_run(**{**kwargs, "approval_digest": "changed"})


def test_transition_is_cas_and_records_attempts(tmp_path):
    ledger = make_ledger(tmp_path)
    run = ledger.create_run(
        platform="x", persona="alice", idempotency_key="k", approval_digest="a", media_manifest_hash="m"
    )
    approved = ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.APPROVED, actor="reviewer")
    assert approved.generation == 1
    with pytest.raises(InvalidTransition):
        ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.APPROVED, actor="reviewer")
    queued = ledger.transition(run.run_id, expected=RunState.APPROVED, target=RunState.QUEUED, actor="reviewer")
    running = ledger.transition(run.run_id, expected=RunState.QUEUED, target=RunState.RUNNING, actor="worker")
    assert queued.state == RunState.QUEUED
    assert running.attempts == 1
    assert [event.to_state for event in ledger.list_events(run.run_id)] == ["prepared", "approved", "queued", "running"]


def test_fencing_lease_blocks_stale_worker(tmp_path):
    ledger = make_ledger(tmp_path)
    run = ledger.create_run(
        platform="x", persona="alice", idempotency_key="k", approval_digest="a", media_manifest_hash="m"
    )
    lease_a = ledger.acquire_lease(run.run_id, "worker-a", ttl_seconds=0.1)
    with pytest.raises(LeaseBusy):
        ledger.acquire_lease(run.run_id, "worker-b", ttl_seconds=60)
    ledger.release_lease(run.run_id, "worker-a", lease_a.fencing_token)
    lease_b = ledger.acquire_lease(run.run_id, "worker-b", ttl_seconds=60)
    with pytest.raises(LeaseRequired):
        ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.APPROVED, actor="worker-a", fencing_token=lease_a.fencing_token)
    transitioned = ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.APPROVED, actor="worker-b", fencing_token=lease_b.fencing_token)
    assert transitioned.state == RunState.APPROVED


def test_effect_outbox_is_idempotent(tmp_path):
    ledger = make_ledger(tmp_path)
    run = ledger.create_run(
        platform="x", persona="alice", idempotency_key="k", approval_digest="a", media_manifest_hash="m"
    )
    first = ledger.record_effect(run.run_id, effect_key="publish:x", effect_type="browser", payload={"url": "u"})
    second = ledger.record_effect(run.run_id, effect_key="publish:x", effect_type="browser", payload={"url": "u"})
    assert first == second
    done = ledger.complete_effect("publish:x", status="succeeded", payload={"remote_id": "r"})
    assert done.status == "succeeded"
    assert done.payload == {"url": "u", "remote_id": "r"}


def test_backup_and_integrity(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.create_run(platform="x", persona="alice", idempotency_key="k", approval_digest="a", media_manifest_hash="m")
    backup = tmp_path / "backup.sqlite3"
    ledger.backup(backup)
    assert backup.is_file()
    assert ledger.integrity_check() == ("ok",)
    restored = PublishLedger(backup)
    assert restored.integrity_check() == ('ok',)
    assert restored.list_runs(persona='alice') == ledger.list_runs(persona='alice')
    assert restored.list_events(restored.list_runs(persona='alice')[0].run_id) == ledger.list_events(ledger.list_runs(persona='alice')[0].run_id)
    assert digest_mapping({"b": 2, "a": 1}) == digest_mapping({"a": 1, "b": 2})


def test_reconciliation_verifies_only_with_scoped_account_and_external_identity(tmp_path):
    ledger = make_ledger(tmp_path)
    run = ledger.create_run(
        platform="x", persona="alice", idempotency_key="k", approval_digest="a", media_manifest_hash="m",
        account_id="alice::x",
    )
    ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.APPROVED, actor="api")
    ledger.transition(run.run_id, expected=RunState.APPROVED, target=RunState.QUEUED, actor="api")
    ledger.transition(run.run_id, expected=RunState.QUEUED, target=RunState.RUNNING, actor="api")
    ledger.transition(run.run_id, expected=RunState.RUNNING, target=RunState.UNKNOWN, actor="publisher")
    with pytest.raises(LeaseRequired):
        ledger.reconcile(run.run_id, account_id="bob::x", outcome="verified", platform_work_id="work-1")
    verified = ledger.reconcile(run.run_id, account_id="alice::x", outcome="verified", platform_work_id="work-1")
    assert verified.state == RunState.PUBLISHED and verified.platform_work_id == "work-1"
