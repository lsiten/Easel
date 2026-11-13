"""Scoped Hypit replication API routes."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from .hypit_lifecycle import HypitLifecycle, LifecycleError, LifecycleRecord
from .hypit_variants import HypitVariantStore, VariantError, VariantNotFoundError

ROOT = Path(__file__).resolve().parents[1] / "outputs" / "_hypit"
LIFECYCLE = HypitLifecycle(ROOT / "runs")
VARIANTS = HypitVariantStore(ROOT / "variants")
router = APIRouter(prefix="/api/hypit", tags=["hypit"])

class Scope(BaseModel):
    session_id: str = Field(min_length=1, max_length=256)
    profile_id: str = Field(min_length=1, max_length=120)

class PlanRequest(Scope):
    kind: str = "sample"
    pricing: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None
    input_fingerprint: str | None = None

class VariantRequest(Scope):
    variant_id: str | None = None
    parent_variant_id: str | None = None
    brief: dict[str, Any] = Field(default_factory=dict)
    treatment: dict[str, Any] = Field(default_factory=dict)
    analysis: dict[str, Any] = Field(default_factory=dict)
    timeline: dict[str, Any] = Field(default_factory=dict)
    sample_target: dict[str, Any] | None = None
    full_target: dict[str, Any] | None = None
    compare_metadata: dict[str, Any] | None = None
    selected_anchors: list[str] = Field(default_factory=list)
    selected_assets: list[str] = Field(default_factory=list)

class ActionRequest(Scope):
    run_id: str | None = None

class StudioRequest(Scope):
    topic: str | None = None
    action: str = "launch"

def _scope(rec: LifecycleRecord, scope: Scope) -> None:
    if rec.session_id != scope.session_id or rec.profile_id != scope.profile_id:
        raise HTTPException(403, "session/profile scope mismatch")

def _safe(value: Any) -> Any:
    """Return API-safe data with no runtime paths or credentials."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            lk = str(k).lower()
            if any(x in lk for x in ("secret", "token", "password", "api_key", "credential")):
                continue
            if lk.endswith("root") or lk in {"path", "runtime_path", "source_root", "output_root"}:
                continue
            out[k] = _safe(v)
        return out
    if isinstance(value, list): return [_safe(v) for v in value]
    return value

def _record(rec: LifecycleRecord) -> dict[str, Any]:
    return _safe(rec.as_dict())

def _get(run_id: str, scope: Scope) -> LifecycleRecord:
    try: rec = LIFECYCLE.get(run_id)
    except Exception as exc: raise HTTPException(404, "run not found") from exc
    _scope(rec, scope)
    return rec

@router.get("/readiness")
def readiness(session_id: str = Query(...), profile_id: str = Query(...)):
    return {"ready": True, "engine": "hypit", "session_id": session_id, "profile_id": profile_id, "scoped": True}

@router.get("/pricing")
def pricing(session_id: str = Query(...), profile_id: str = Query(...), kind: str = "sample", units: int = 1):
    try: value = LIFECYCLE.pricing(profile_id, kind=kind, units=units)
    except LifecycleError as exc: raise HTTPException(400, str(exc)) from exc
    value["session_id"] = session_id
    return _safe(value)

@router.post("/runs")
def plan_run(req: PlanRequest):
    pricing_receipt = req.pricing or LIFECYCLE.pricing(req.profile_id, kind=req.kind)
    try: rec = LIFECYCLE.plan(req.profile_id, req.session_id, kind=req.kind, pricing=pricing_receipt, checkpoint=req.checkpoint, input_fingerprint=req.input_fingerprint)
    except LifecycleError as exc: raise HTTPException(400, str(exc)) from exc
    return _record(rec)

@router.get("/runs/{run_id}")
def run_status(run_id: str, session_id: str = Query(...), profile_id: str = Query(...)):
    return _record(_get(run_id, Scope(session_id=session_id, profile_id=profile_id)))

@router.get("/runs/{run_id}/status")
def run_status_alias(run_id: str, session_id: str = Query(...), profile_id: str = Query(...)):
    return run_status(run_id, session_id, profile_id)

@router.get("/runs/{run_id}/logs")
def run_logs(run_id: str, session_id: str = Query(...), profile_id: str = Query(...), offset: int = 0, limit: int = 100):
    rec = _get(run_id, Scope(session_id=session_id, profile_id=profile_id))
    if offset < 0 or limit < 1 or limit > 500: raise HTTPException(400, "invalid pagination")
    return {"run_id": run_id, "offset": offset, "limit": limit, "logs": rec.logs[offset:offset + limit], "next_offset": min(offset + limit, len(rec.logs))}

@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, req: ActionRequest):
    if req.run_id and req.run_id != run_id: raise HTTPException(400, "run_id mismatch")
    rec = _get(run_id, req)
    try: rec = LIFECYCLE.cancel(run_id)
    except Exception as exc: raise HTTPException(404, "run not found") from exc
    return _record(rec)

@router.post("/runs/{run_id}/rerun")
def rerun_run(run_id: str, req: ActionRequest):
    if req.run_id and req.run_id != run_id: raise HTTPException(400, "run_id mismatch")
    rec = _get(run_id, req)
    try: rec = LIFECYCLE.retry(run_id)
    except LifecycleError as exc: raise HTTPException(409, str(exc)) from exc
    return _record(rec)

@router.post("/runs/{run_id}/analysis")
def run_analysis(run_id: str, req: ActionRequest):
    return _record(_get(run_id, req))

@router.post("/runs/{run_id}/timeline")
def run_timeline(run_id: str, req: ActionRequest):
    return _record(_get(run_id, req))

@router.get("/runs/{run_id}/reports")
def run_reports(run_id: str, session_id: str = Query(...), profile_id: str = Query(...)):
    rec = _get(run_id, Scope(session_id=session_id, profile_id=profile_id))
    return {"run_id": run_id, "reports": [{"id": run_id, "state": rec.state, "checkpoint": _safe(rec.checkpoint)}]}

@router.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: str, session_id: str = Query(...), profile_id: str = Query(...)):
    rec = _get(run_id, Scope(session_id=session_id, profile_id=profile_id))
    return {"run_id": run_id, "artifacts": [{"name": Path(p).name, "kind": "artifact"} for p in rec.outputs]}

@router.post("/variants")
def create_variant(req: VariantRequest):
    try: rec = VARIANTS.create_variant(**req.model_dump())
    except VariantError as exc: raise HTTPException(400, str(exc)) from exc
    return _safe(rec.to_dict())

@router.get("/variants")
def list_variants(session_id: str = Query(...), profile_id: str = Query(...)):
    return {"variants": [_safe(v.to_dict()) for v in VARIANTS.list(session_id=session_id, profile_id=profile_id)], "session_id": session_id, "profile_id": profile_id}

@router.get("/variants/{variant_id}")
def get_variant(variant_id: str, session_id: str = Query(...), profile_id: str = Query(...)):
    try: rec = VARIANTS.get(variant_id)
    except VariantNotFoundError as exc: raise HTTPException(404, "variant not found") from exc
    if (rec.session_id, rec.profile_id) != (session_id, profile_id): raise HTTPException(403, "session/profile scope mismatch")
    return _safe(rec.to_dict())

@router.get("/studio")
def studio(session_id: str = Query(...), profile_id: str = Query(...)):
    return {"session_id": session_id, "profile_id": profile_id, "available": False, "studio": "", "scoped": True}

@router.post("/studio/launch")
def studio_launch(req: StudioRequest):
    if req.action not in {"launch", "status", "control"}: raise HTTPException(400, "invalid studio action")
    return {"session_id": req.session_id, "profile_id": req.profile_id, "launched": False, "reason": "openfilm delegation required", "scoped": True}
