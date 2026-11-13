"""Scoped Hypit replication API routes."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from .hypit_lifecycle import HypitLifecycle, LifecycleError, LifecycleRecord, RuntimeNotReady
from .hypit_variants import HypitVariantStore, VariantError, VariantNotFoundError
from .video_artifact_boundary import ArtifactBoundaryError, import_artifacts
from .hypit_studio import (embed_url, frame_ancestors, issue_token, license_available,
                          origin_allowed, policy_for_url, studio_url, validate_token)

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
    run_id: str | None = None
    token: str | None = None
    nonce: str | None = None
    engine: str = "hypit"
    source: str = "easel"

class AnalysisRequest(Scope):
    run_id: str | None = None
    analysis: dict[str, Any] | None = None
    data: dict[str, Any] | None = None

class TimelineRequest(Scope):
    run_id: str | None = None
    timeline: dict[str, Any] | None = None
    data: dict[str, Any] | None = None

class ArtifactImportRequest(Scope):
    run_id: str
    artifact: str = Field(min_length=1, max_length=512)

def _scope(rec: LifecycleRecord, scope: Scope) -> None:
    if rec.session_id != scope.session_id or rec.profile_id != scope.profile_id:
        raise HTTPException(403, "session/profile scope mismatch")

_SECRET_TEXT = re.compile(r"(?i)(?:bearer\s+|api[_ -]?key\s*[=:]\s*|token\s*[=:]\s*|password\s*[=:]\s*)[^\s,;]+")
_PATH_TEXT = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\|/tmp/)[^\s,;]+")

def _safe(value: Any) -> Any:
    """Return API-safe data with no runtime paths or credentials, recursively."""
    if isinstance(value, str):
        value = _SECRET_TEXT.sub("[REDACTED]", value)
        return _PATH_TEXT.sub("[PATH_REDACTED]", value)
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
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", run_id):
        raise HTTPException(404, "run not found")
    try: rec = LIFECYCLE.get(run_id)
    except Exception as exc: raise HTTPException(404, "run not found") from exc
    _scope(rec, scope)
    return rec

@router.get("/readiness")
def readiness(engine: str = Query(...), session_id: str = Query(..., min_length=1, max_length=256), profile_id: str = Query(..., min_length=1, max_length=120)):
    if engine != "hypit":
        raise HTTPException(400, "engine scope mismatch")
    result = LIFECYCLE.readiness(profile_id, session_id)
    return {**_safe(result), "ready": bool(result.get("ok")), "engine": engine, "session_id": session_id, "profile_id": profile_id, "scoped": True}

@router.get("/pricing")
def pricing(engine: str = Query(...), session_id: str = Query(..., min_length=1, max_length=256), profile_id: str = Query(..., min_length=1, max_length=120), kind: str = "sample", units: int = 1):
    if engine != "hypit":
        raise HTTPException(400, "engine scope mismatch")
    try: value = LIFECYCLE.pricing(profile_id, kind=kind, units=units)
    except LifecycleError as exc: raise HTTPException(400, str(exc)) from exc
    value["session_id"] = session_id
    value["engine"] = "hypit"
    return _safe(value)

@router.post("/runs")
def plan_run(req: PlanRequest):
    if not req.pricing:
        raise HTTPException(400, "pricing receipt required")
    pricing_receipt = req.pricing
    if pricing_receipt.get("engine") != "hypit":
        raise HTTPException(403, "pricing engine mismatch")
    if pricing_receipt.get("session_id") != req.session_id or pricing_receipt.get("profile_id") != req.profile_id:
        raise HTTPException(403, "pricing scope mismatch")
    if req.input_fingerprint:
        for rid in LIFECYCLE._ids():
            try:
                existing = LIFECYCLE._load(rid)
            except Exception:
                continue
            if (existing.profile_id, existing.session_id, existing.input_fingerprint) != (req.profile_id, req.session_id, req.input_fingerprint):
                continue
            if existing.kind != req.kind or (req.checkpoint is not None and existing.checkpoint != req.checkpoint):
                raise HTTPException(409, "input fingerprint conflicts with existing run")
            return _record(existing)
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
    return {"run_id": run_id, "offset": offset, "limit": limit, "logs": _safe(rec.logs[offset:offset + limit]), "next_offset": min(offset + limit, len(rec.logs))}

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
    except RuntimeNotReady as exc: raise HTTPException(409, {"code": exc.code, "message": str(exc), "details": _safe(exc.details)}) from exc
    except LifecycleError as exc: raise HTTPException(409, str(exc)) from exc
    return _record(rec)

@router.post("/runs/{run_id}/run")
def start_run(run_id: str, req: ActionRequest):
    """Start a planned run after the caller's session/profile scope is verified."""
    if req.run_id and req.run_id != run_id: raise HTTPException(400, "run_id mismatch")
    _get(run_id, req)
    try: return _record(LIFECYCLE.run(run_id))
    except RuntimeNotReady as exc: raise HTTPException(409, {"code": exc.code, "message": str(exc), "details": _safe(exc.details)}) from exc
    except LifecycleError as exc: raise HTTPException(409, str(exc)) from exc

@router.post("/runs/{run_id}/analysis")
def run_analysis(run_id: str, req: AnalysisRequest):
    if req.run_id and req.run_id != run_id:
        raise HTTPException(400, "run_id mismatch")
    rec = _get(run_id, req)
    payload = req.analysis if req.analysis is not None else req.data
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(400, "analysis payload must be non-empty")
    rec.checkpoint["analysis"] = _safe(payload)
    rec.updated_at = LIFECYCLE.clock()
    LIFECYCLE._save(rec)
    return {"run_id": run_id, "analysis": _safe(rec.checkpoint["analysis"]), "status": _record(rec)}

@router.post("/runs/{run_id}/timeline")
def run_timeline(run_id: str, req: TimelineRequest):
    if req.run_id and req.run_id != run_id:
        raise HTTPException(400, "run_id mismatch")
    rec = _get(run_id, req)
    payload = req.timeline if req.timeline is not None else req.data
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(400, "timeline payload must be non-empty")
    rec.checkpoint["timeline"] = _safe(payload)
    rec.updated_at = LIFECYCLE.clock()
    LIFECYCLE._save(rec)
    return {"run_id": run_id, "timeline": _safe(rec.checkpoint["timeline"]), "status": _record(rec)}

@router.get("/runs/{run_id}/reports")
def run_reports(run_id: str, session_id: str = Query(...), profile_id: str = Query(...)):
    rec = _get(run_id, Scope(session_id=session_id, profile_id=profile_id))
    return {"run_id": run_id, "reports": [{"id": run_id, "state": rec.state, "checkpoint": _safe(rec.checkpoint)}]}

@router.get("/runs/{run_id}/artifacts")
def run_artifacts(run_id: str, session_id: str = Query(...), profile_id: str = Query(...)):
    rec = _get(run_id, Scope(session_id=session_id, profile_id=profile_id))
    return {"run_id": run_id, "artifacts": [{"name": Path(p).name, "kind": "artifact"} for p in rec.outputs]}

@router.post("/artifacts/import")
def import_artifact(req: ArtifactImportRequest):
    """Import one declared derived Hypit artifact into the shared output surface."""
    rec = _get(req.run_id, req)
    if rec.state != "succeeded": raise HTTPException(409, f"artifacts unavailable while {rec.state}")
    source_root = ROOT / "runs" / req.run_id
    candidates = []
    for raw in rec.outputs:
        path = Path(raw).expanduser().resolve()
        try: relative = path.relative_to(source_root.resolve()).as_posix()
        except ValueError: continue
        if path.is_file() and path.name == req.artifact: candidates.append((path, relative))
    if len(candidates) != 1: raise HTTPException(404, "artifact not found")
    path, relative = candidates[0]
    import hashlib
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    kind = {".mp4": "mp4", ".png": "thumbnail", ".jpg": "thumbnail", ".jpeg": "thumbnail", ".webp": "thumbnail", ".md": "brief", ".txt": "brief", ".html": "report", ".pdf": "report", ".json": "report"}.get(path.suffix.lower())
    if not kind: raise HTTPException(400, "unsupported artifact type")
    manifest = {"artifacts": [{"kind": kind, "path": relative, "checksum": digest, "attribution": "Hypit derived artifact"}]}
    try:
        records = import_artifacts(source_root=source_root, destination_root=ROOT.parent, source_engine="hypit", target_profile=req.profile_id, manifest=manifest)
    except ArtifactBoundaryError as exc: raise HTTPException(400, str(exc)) from exc
    return {"imported": True, "run_id": req.run_id, "artifact": _safe(records[0].to_dict())}

@router.post("/runs/{run_id}/artifacts/import")
def import_run_artifact(run_id: str, req: ArtifactImportRequest):
    if req.run_id != run_id: raise HTTPException(400, "run_id mismatch")
    return import_artifact(req)

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

@router.post("/variants/{variant_id}/approve")
def approve_variant(variant_id: str, req: Scope):
    """Approve the current sample fingerprint for one scoped variant."""
    try: rec = VARIANTS.get(variant_id)
    except VariantNotFoundError as exc: raise HTTPException(404, "variant not found") from exc
    if (rec.session_id, rec.profile_id) != (req.session_id, req.profile_id):
        raise HTTPException(403, "session/profile scope mismatch")
    try: return _safe(VARIANTS.approve_sample(variant_id).to_dict())
    except VariantError as exc: raise HTTPException(409, str(exc)) from exc

def _studio_origin_allowed(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin_allowed(origin):
        raise HTTPException(403, "studio origin is not allowlisted")


def _studio_authorize(request: Request, req: StudioRequest) -> LifecycleRecord:
    if not req.run_id:
        raise HTTPException(403, "studio run required")
    _studio_origin_allowed(request)
    try:
        record = _get(req.run_id, req)
    except HTTPException:
        raise
    if not _studio_ready(record):
        raise HTTPException(403, "studio run is not ready")
    if not license_available():
        raise HTTPException(403, "Hypit Studio license unavailable")
    if not policy_for_url(studio_url()):
        raise HTTPException(403, "studio origin is not allowlisted")
    return record


def _studio_scope_record(run_id: str | None, scope: Scope) -> LifecycleRecord | None:
    if not run_id:
        return None
    return _get(run_id, scope)


def _studio_ready(record: LifecycleRecord) -> bool:
    """A run must still belong to the requested session and be in a usable state."""
    return record.state in {"planned", "running", "built", "succeeded", "failed"}


def _studio_payload(scope: Scope, run_id: str | None, request: Request) -> dict[str, Any]:
    """Issue one scoped Studio URL only after run, readiness, license and origin checks."""
    url = studio_url()
    result: dict[str, Any] = {
        "session_id": scope.session_id,
        "profile_id": scope.profile_id,
        "run_id": run_id or "",
        "available": False,
        "studio": "",
        "external_url": url,
        "scoped": True,
        "engine": "hypit",
        "source": "easel",
        "frame_ancestors": frame_ancestors(),
    }
    if not run_id:
        return result
    rec = _studio_scope_record(run_id, scope)
    if rec is None or not _studio_ready(rec):
        return result
    if not license_available() or not url or not policy_for_url(url):
        return result
    issued = issue_token(run_id=run_id, profile_id=scope.profile_id, session_id=scope.session_id)
    result.update({
        "available": True,
        "studio": embed_url(url, issued["token"], run_id),
        "external_url": url,
        "token": issued["token"],
        "nonce": issued["claims"]["nonce"],
        "expires_at": issued["claims"]["exp"],
        "run_id": run_id,
    })
    return result


@router.get("/studio")
def studio(request: Request, response: Response, session_id: str = Query(...), profile_id: str = Query(...), run_id: str | None = Query(default=None)):
    response.headers["Content-Security-Policy"] = frame_ancestors()
    return _studio_payload(Scope(session_id=session_id, profile_id=profile_id), run_id, request)


@router.post("/studio/handshake")
def studio_handshake(request: Request, req: StudioRequest):
    if req.action != "handshake" or not req.run_id or not req.token or not req.nonce:
        raise HTTPException(400, "handshake requires run_id and token")
    if req.engine != "hypit" or req.source != "easel":
        raise HTTPException(403, "studio identity mismatch")
    rec = _studio_authorize(request, req)
    try:
        claims = validate_token(req.token, run_id=req.run_id, profile_id=req.profile_id, session_id=req.session_id, nonce=req.nonce)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"accepted": True, "run_id": req.run_id, "session_id": req.session_id, "profile_id": req.profile_id, "engine": claims["engine"], "source": claims["source"], "nonce": claims["nonce"]}


@router.post("/studio/launch")
def studio_launch(request: Request, req: StudioRequest):
    if req.action not in {"launch", "status", "control", "refresh", "open", "cancel"}:
        raise HTTPException(400, "invalid studio action")
    if req.engine != "hypit" or req.source != "easel":
        raise HTTPException(403, "studio identity mismatch")
    if not req.run_id or not req.token or not req.nonce:
        raise HTTPException(403, "studio token and nonce required")
    rec = _studio_authorize(request, req)
    try:
        claims = validate_token(req.token, run_id=req.run_id, profile_id=req.profile_id, session_id=req.session_id, nonce=req.nonce)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    if req.action == "cancel":
        rec = LIFECYCLE.cancel(req.run_id)
    return {"session_id": req.session_id, "profile_id": req.profile_id, "run_id": req.run_id,
            "launched": req.action in {"launch", "open"}, "action": req.action, "engine": claims["engine"],
            "source": claims["source"], "scoped": True, "state": rec.state}


@router.post("/studio/control")
def studio_control(request: Request, req: StudioRequest):
    if req.action not in {"control", "refresh", "open", "cancel"}:
        raise HTTPException(400, "invalid studio action")
    return studio_launch(request, req)
