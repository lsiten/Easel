"""Resumable Hypit Plan/Run/Build/Result lifecycle.

The service is deliberately provider-neutral and persists every transition under a
run directory.  OpenFilm callers remain unaffected because this module is opt-in.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

STATES = ("planned", "running", "built", "succeeded", "failed", "cancelled")
_TERMINAL = {"succeeded", "failed", "cancelled"}
_TRANSITIONS = {
    "planned": {"running", "cancelled"},
    "running": {"built", "failed", "cancelled"},
    "built": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": {"planned", "running"},
    "cancelled": {"planned", "running"},
}

class LifecycleError(RuntimeError):
    pass
class InvalidTransition(LifecycleError):
    pass
class QuotaExceeded(LifecycleError):
    pass
class LifecycleTimeout(LifecycleError):
    pass
class OutputValidationError(LifecycleError):
    pass

@dataclass
class LifecycleRecord:
    id: str
    profile_id: str
    session_id: str
    kind: str
    state: str = "planned"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    run_id: str = ""
    attempt: int = 0
    checkpoint: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    input_fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LifecycleRecord":
        return cls(**{k: value[k] for k in cls.__dataclass_fields__ if k in value})

Runner = Callable[..., Mapping[str, Any] | None]

class HypitLifecycle:
    """Durable lifecycle service for one workspace.

    ``runner`` may accept ``(record, checkpoint, cancel_event)`` and return a mapping
    containing ``checkpoint``, ``outputs`` and ``logs``.  A runner accepting fewer
    arguments is also supported for test doubles.
    """
    def __init__(self, root: Path | str, *, runner: Runner | None = None,
                 clock: Callable[[], float] = time.time, max_active_per_profile: int = 2,
                 timeout_seconds: int = 7200):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner = runner or self._default_runner
        self.clock = clock
        self.max_active_per_profile = max_active_per_profile
        self.timeout_seconds = min(int(timeout_seconds), 7200)
        if self.timeout_seconds <= 0:
            raise LifecycleError("timeout_seconds must be positive")
        self._lock = threading.RLock()
        self._cancel: dict[str, threading.Event] = {}

    def _path(self, rid: str) -> Path:
        return self.root / rid / "lifecycle.json"
    def _save(self, record: LifecycleRecord) -> None:
        target = self._path(record.id); target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    def get(self, record_id: str) -> LifecycleRecord:
        return LifecycleRecord.from_dict(json.loads(self._path(record_id).read_text(encoding="utf-8")))
    def _transition(self, rec: LifecycleRecord, state: str) -> None:
        if state not in _TRANSITIONS.get(rec.state, set()):
            raise InvalidTransition(f"{rec.state} -> {state}")
        rec.state = state; rec.updated_at = self.clock(); self._save(rec)

    def pricing(self, profile_id: str, *, kind: str = "sample", units: int = 1) -> dict[str, Any]:
        if kind not in {"sample", "full"} or units < 1:
            raise LifecycleError("kind must be sample/full and units positive")
        unit_cost = 1 if kind == "sample" else 4
        return {"profile_id": profile_id, "kind": kind, "units": units, "unit_cost": unit_cost,
                "estimated_cost": unit_cost * units, "pricing_id": f"hypit-{kind}-{unit_cost}",
                "valid_until": self.clock() + 600}

    def plan(self, profile_id: str, session_id: str, *, kind: str = "sample", pricing: Mapping[str, Any] | None = None,
             checkpoint: Mapping[str, Any] | None = None, input_fingerprint: str | None = None) -> LifecycleRecord:
        if kind not in {"sample", "full"}: raise LifecycleError("kind must be sample or full")
        if not isinstance(pricing, Mapping):
            raise LifecycleError("valid pricing receipt required")
        required = {"pricing_id", "profile_id", "kind", "valid_until", "estimated_cost"}
        if not required.issubset(pricing) or pricing.get("profile_id") != profile_id or pricing.get("kind") != kind:
            raise LifecycleError("malformed pricing receipt")
        try:
            pricing_id = str(pricing["pricing_id"])
            units = float(pricing.get("units", 1))
            unit_cost = float(pricing.get("unit_cost"))
            estimated = float(pricing["estimated_cost"])
            import math
            expected_unit = 1.0 if kind == "sample" else 4.0
            if not pricing_id or not pricing_id.startswith(f"hypit-{kind}-"):
                raise LifecycleError("malformed pricing receipt")
            if not (math.isfinite(units) and units > 0 and units.is_integer()):
                raise LifecycleError("malformed pricing units")
            if not (math.isfinite(unit_cost) and unit_cost > 0 and abs(unit_cost - expected_unit) < 1e-9):
                raise LifecycleError("inconsistent pricing unit cost")
            if not math.isfinite(estimated) or abs(estimated - units * unit_cost) > 1e-9:
                raise LifecycleError("inconsistent pricing estimate")
            if float(pricing["valid_until"]) <= self.clock():
                raise LifecycleError("stale or invalid pricing receipt")
        except (TypeError, ValueError) as exc:
            raise LifecycleError("malformed pricing receipt") from exc
        fingerprint = input_fingerprint or hashlib.sha256(f"{profile_id}:{session_id}:{kind}".encode()).hexdigest()
        if not isinstance(fingerprint, str) or len(fingerprint) < 8:
            raise LifecycleError("invalid input fingerprint")
        if checkpoint:
            self._validate_checkpoint(dict(checkpoint), fingerprint, 0)
        with self._lock:
            active = [self._load(p) for p in self._ids() if self._load(p).profile_id == profile_id and self._load(p).state not in _TERMINAL]
            if any(r.session_id == session_id for r in active): raise QuotaExceeded("one active Build/session")
            if len(active) >= self.max_active_per_profile: raise QuotaExceeded("profile build quota exceeded")
            rid = uuid.uuid4().hex; rec = LifecycleRecord(rid, profile_id, session_id, kind, run_id=uuid.uuid4().hex,
                checkpoint=dict(checkpoint or {}), logs=["planned"], input_fingerprint=fingerprint)
            self._save(rec); return rec

    def _ids(self) -> list[str]:
        return [p.parent.name for p in self.root.glob("*/lifecycle.json")]
    def _load(self, rid: str) -> LifecycleRecord: return self.get(rid)

    def run(self, record_id: str, *, runner: Runner | None = None) -> LifecycleRecord:
        with self._lock:
            rec = self.get(record_id)
            if rec.state == "succeeded": return rec
            if rec.state not in {"planned", "failed", "cancelled"}: raise InvalidTransition(f"cannot run from {rec.state}")
            if rec.state in {"failed", "cancelled"}: rec.state = "planned"; rec.updated_at = self.clock(); self._save(rec)
            self._validate_checkpoint(rec.checkpoint, rec.input_fingerprint, 0)
            self._transition(rec, "running")
            rec.attempt += 1; rec.logs.append(f"attempt {rec.attempt}"); self._save(rec)
            event = threading.Event(); timeout_event = threading.Event(); self._cancel[record_id] = event
            started = self.clock()
            def expire() -> None:
                timeout_event.set(); event.set()
            timer = threading.Timer(self.timeout_seconds, expire); timer.daemon = True; timer.start()
        try:
            fn = runner or self.runner
            try: result = fn(rec, dict(rec.checkpoint), event)
            except TypeError: result = fn(rec, dict(rec.checkpoint))
            result = dict(result or {})
            if timeout_event.is_set() or self.clock() - started > self.timeout_seconds:
                raise LifecycleTimeout("build timeout")
            if event.is_set(): raise LifecycleError("cancelled")
            incoming = dict(result.get("checkpoint") or {})
            if incoming:
                incoming.setdefault("revision", int(rec.checkpoint.get("revision", 0)) + 1)
                incoming.setdefault("schema", 1)
                incoming.setdefault("fingerprint", rec.input_fingerprint)
                self._validate_checkpoint(incoming, rec.input_fingerprint, int(rec.checkpoint.get("revision", 0)))
                rec.checkpoint.update(incoming)
            rec.outputs = [str(x) for x in result.get("outputs", rec.outputs)]
            rec.logs.extend(str(x) for x in result.get("logs", []))
            self._save(rec)
            self._transition(rec, "built")
            self._validate_outputs(rec)
            self._transition(rec, "succeeded")
        except LifecycleTimeout as exc:
            rec.error = str(exc); rec.logs.append(str(exc)); self._save(rec)
            if rec.state not in _TERMINAL: self._transition(rec, "failed")
        except LifecycleError as exc:
            rec.error = str(exc); rec.logs.append(str(exc)); self._save(rec)
            target = "cancelled" if str(exc) == "cancelled" else "failed"
            if rec.state not in _TERMINAL: self._transition(rec, target)
        except Exception as exc:
            rec.error = str(exc); rec.logs.append(str(exc)); self._save(rec)
            if rec.state not in _TERMINAL: self._transition(rec, "failed")
        finally:
            timer.cancel()
            self._cancel.pop(record_id, None)
        return self.get(record_id)

    def build(self, record_id: str, **kwargs: Any) -> LifecycleRecord: return self.run(record_id, **kwargs)
    def cancel(self, record_id: str) -> LifecycleRecord:
        with self._lock:
            rec = self.get(record_id)
            if rec.state in _TERMINAL: return rec
            event = self._cancel.get(record_id)
            if event: event.set()
            if rec.state in {"planned", "running"}:
                rec.error = "cancelled"; rec.logs.append("cancelled"); self._save(rec)
                self._transition(rec, "cancelled")
            return rec
    def retry(self, record_id: str, **kwargs: Any) -> LifecycleRecord:
        rec = self.get(record_id)
        if rec.state == "succeeded": return rec
        if rec.state not in {"failed", "cancelled"}: raise InvalidTransition("retry requires failed or cancelled")
        return self.run(record_id, **kwargs)
    def status(self, record_id: str) -> dict[str, Any]: return self.get(record_id).as_dict()
    def logs(self, record_id: str) -> list[str]: return list(self.get(record_id).logs)
    def inspect(self, record_id: str) -> dict[str, Any]:
        rec = self.get(record_id); return {"state": rec.state, "checkpoint": dict(rec.checkpoint), "outputs": list(rec.outputs), "error": rec.error}
    def result(self, record_id: str) -> dict[str, Any]:
        rec = self.get(record_id)
        if rec.state != "succeeded": raise LifecycleError(f"result unavailable while {rec.state}")
        return {"id": rec.id, "outputs": list(rec.outputs), "checkpoint": dict(rec.checkpoint)}
    @staticmethod
    def _validate_checkpoint(checkpoint: Mapping[str, Any], fingerprint: str, minimum_revision: int) -> None:
        if not isinstance(checkpoint, Mapping): raise LifecycleError("malformed checkpoint")
        if not checkpoint: return
        required = {"revision", "schema", "stage", "fingerprint"}
        if not required.issubset(checkpoint): raise LifecycleError("malformed checkpoint")
        if checkpoint.get("schema") != 1 or not isinstance(checkpoint.get("revision"), int):
            raise LifecycleError("malformed checkpoint schema")
        if checkpoint["revision"] < minimum_revision or checkpoint["revision"] < 0:
            raise LifecycleError("stale checkpoint revision")
        if checkpoint.get("fingerprint") != fingerprint:
            raise LifecycleError("stale checkpoint fingerprint")
        if checkpoint.get("stage") not in {"planned", "sample", "full", "rendered", "complete", "partial", "retry"}:
            raise LifecycleError("invalid checkpoint stage")

    def _validate_outputs(self, rec: LifecycleRecord) -> None:
        if not rec.outputs:
            raise OutputValidationError("no durable outputs produced")
        for output in rec.outputs:
            p = Path(output)
            if not p.is_absolute(): p = self.root / rec.id / p
            if not p.is_file() or p.stat().st_size <= 0: raise OutputValidationError(f"durable output missing: {output}")
    @staticmethod
    def _default_runner(rec: LifecycleRecord, checkpoint: Mapping[str, Any], cancel: threading.Event) -> Mapping[str, Any]:
        return {"checkpoint": {"stage": "complete"}, "outputs": [], "logs": ["build complete"]}

__all__ = ["HypitLifecycle", "LifecycleRecord", "LifecycleError", "InvalidTransition", "QuotaExceeded", "LifecycleTimeout", "OutputValidationError"]
