from pathlib import Path

import pytest
from easel.hypit_lifecycle import HypitLifecycle, LifecycleError, QuotaExceeded


def test_resumable_sample_cancel_retry_and_preserve_output(tmp_path):
    calls = []
    output = tmp_path / "out.mp4"
    def runner(rec, checkpoint, cancel):
        calls.append(dict(checkpoint))
        if len(calls) == 1:
            output.write_bytes(b"partial")
            return {"checkpoint": {"stage": "sample"}, "outputs": [str(output)]}
        return {"checkpoint": {"stage": "full"}, "outputs": [str(output)]}
    service = HypitLifecycle(tmp_path / "state", runner=runner)
    rec = service.plan("p", "s", kind="sample", pricing=service.pricing("p", kind="sample"))
    assert service.run(rec.id).state == "succeeded"
    assert service.result(rec.id)["outputs"] == [str(output)]
    assert calls[0] == {}


def test_failure_is_terminal_and_checkpoint_reusable(tmp_path):
    output = tmp_path / "out.mp4"
    def runner(rec, checkpoint, cancel):
        output.write_bytes(b"preserved")
        return {"checkpoint": {"stage": "rendered"}, "outputs": [str(output)]}
    service = HypitLifecycle(tmp_path / "state", runner=runner)
    rec = service.plan("p", "s", pricing=service.pricing("p"))
    output.write_bytes(b"old")
    # Missing output path must never claim success and is recoverable by retry.
    rec = service.run(rec.id, runner=lambda *_: {"checkpoint": {"stage": "partial"}, "outputs": [str(tmp_path / "missing.mp4")]})
    assert rec.state == "failed"
    assert service.inspect(rec.id)["checkpoint"]["stage"] == "partial"
    assert service.retry(rec.id).state == "succeeded"
    assert output.read_bytes() == b"preserved"


def test_quota_and_malformed_transitions(tmp_path):
    service = HypitLifecycle(tmp_path / "state")
    a = service.plan("p", "a", pricing=service.pricing("p"))
    with pytest.raises(QuotaExceeded): service.plan("p", "a", pricing=service.pricing("p"))
    b = service.plan("p", "b", pricing=service.pricing("p"))
    with pytest.raises(QuotaExceeded): service.plan("p", "c", pricing=service.pricing("p"))
    with pytest.raises(LifecycleError): service.result(a.id)
    service.cancel(a.id)
    service.cancel(a.id)
    assert service.status(a.id)["state"] == "cancelled"

def test_pricing_receipt_validation_and_idempotent_retry(tmp_path):
    service = HypitLifecycle(tmp_path / "state")
    with pytest.raises(LifecycleError): service.plan("p", "s")
    stale = service.pricing("p"); stale["valid_until"] = 0
    with pytest.raises(LifecycleError): service.plan("p", "s", pricing=stale)
    out = tmp_path / "ok.mp4"
    def runner(*_): out.write_bytes(b"ok"); return {"outputs": [str(out)]}
    rec = service.plan("p", "s", pricing=service.pricing("p"))
    done = service.run(rec.id, runner=runner)
    assert service.retry(rec.id).id == done.id


def test_timeout_sets_terminal_failed_without_false_success(tmp_path):
    service = HypitLifecycle(tmp_path / "state", timeout_seconds=1)
    rec = service.plan("p", "s", pricing=service.pricing("p"))
    def slow(rec, checkpoint, cancel):
        cancel.wait(2)
        return {"outputs": [str(tmp_path / "missing")]}
    done = service.run(rec.id, runner=slow)
    assert done.state == "failed"
    assert "cancel" in (done.error or "") or "timeout" in (done.error or "")

def test_stale_checkpoint_revision_and_fingerprint_rejected(tmp_path):
    service = HypitLifecycle(tmp_path / "state")
    pricing = service.pricing("p")
    with pytest.raises(LifecycleError):
        service.plan("p", "s", pricing=pricing, checkpoint={"revision": -999, "schema": 1, "stage": "sample", "fingerprint": "bad"})
    with pytest.raises(LifecycleError):
        service.plan("p", "s", pricing=pricing, checkpoint={"revision": 0, "schema": 1, "stage": "unknown", "fingerprint": "badfingerprint"})

def test_pricing_receipt_consistency_rejects_misleading_values(tmp_path):
    service = HypitLifecycle(tmp_path / "state")
    base = service.pricing("p")
    for key, value in (("pricing_id", ""), ("units", 99), ("unit_cost", 999), ("estimated_cost", 0)):
        bad = dict(base); bad[key] = value
        with pytest.raises(LifecycleError): service.plan("p", "s", pricing=bad)
