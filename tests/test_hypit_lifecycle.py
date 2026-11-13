from pathlib import Path

import pytest
from easel.hypit_lifecycle import HypitLifecycle, InvalidTransition, QuotaExceeded


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
    rec = service.plan("p", "s", kind="sample")
    assert service.run(rec.id).state == "succeeded"
    assert service.result(rec.id)["outputs"] == [str(output)]
    assert calls[0] == {}


def test_failure_is_terminal_and_checkpoint_reusable(tmp_path):
    output = tmp_path / "out.mp4"
    def runner(rec, checkpoint, cancel):
        output.write_bytes(b"preserved")
        return {"checkpoint": {"stage": "rendered"}, "outputs": [str(output)]}
    service = HypitLifecycle(tmp_path / "state", runner=runner)
    rec = service.plan("p", "s")
    output.write_bytes(b"old")
    # Missing output path must never claim success and is recoverable by retry.
    rec = service.run(rec.id, runner=lambda *_: {"checkpoint": {"stage": "partial"}, "outputs": [str(tmp_path / "missing.mp4")]})
    assert rec.state == "failed"
    assert service.inspect(rec.id)["checkpoint"]["stage"] == "partial"
    assert service.retry(rec.id).state == "succeeded"
    assert output.read_bytes() == b"preserved"


def test_quota_and_malformed_transitions(tmp_path):
    service = HypitLifecycle(tmp_path / "state")
    a = service.plan("p", "a")
    with pytest.raises(QuotaExceeded): service.plan("p", "a")
    b = service.plan("p", "b")
    with pytest.raises(QuotaExceeded): service.plan("p", "c")
    with pytest.raises(InvalidTransition): service.result(a.id)
    service.cancel(a.id)
    service.cancel(a.id)
    assert service.status(a.id)["state"] == "cancelled"
