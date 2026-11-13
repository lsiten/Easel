"""Durable Hypit concept variants and sample approval gates.

This module is opt-in.  It stores Hypit branches under an explicit root and does
not touch OpenFilm manifests or output directories.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SUSPICIOUS = re.compile(r"(?i)(ignore\s+(?:all\s+)?previous\s+instructions|system\s*prompt|developer\s+message|jailbreak|<\s*(?:system|instruction)\b)")

class VariantError(ValueError):
    pass
class BranchOverwriteError(VariantError):
    pass
class VariantExistsError(BranchOverwriteError):
    pass
class VariantNotFoundError(VariantError):
    pass
class ApprovalRequiredError(VariantError):
    pass


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise VariantError(f"invalid {label}")
    return value

def _text(value: Any, label: str, *, max_length: int = 20000) -> str:
    if not isinstance(value, str) or len(value) > max_length or any(ord(ch) < 32 and ch not in "\n\t" for ch in value):
        raise VariantError(f"invalid {label}")
    if _SUSPICIOUS.search(value):
        raise VariantError(f"unsafe {label}")
    return value

def _json_value(value: Any, label: str = "concept") -> Any:
    if isinstance(value, str):
        return _text(value, label)
    if isinstance(value, Mapping):
        return {str(_text(str(k), f"{label} key", max_length=200)): _json_value(v, label) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_value(v, label) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    raise VariantError(f"invalid {label}")

def _root(value: Path | str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise VariantError(f"{label} must be absolute")
    return path.resolve()

@dataclass
class VariantRecord:
    variant_id: str
    profile_id: str
    session_id: str
    parent_variant_id: str | None
    brief: dict[str, Any]
    treatment: dict[str, Any]
    analysis: dict[str, Any]
    timeline: dict[str, Any]
    sample_target: dict[str, Any]
    full_target: dict[str, Any]
    approval_state: str = "pending"
    approval: dict[str, Any] | None = None
    compare_metadata: dict[str, Any] = field(default_factory=dict)
    selected_anchors: list[str] = field(default_factory=list)
    selected_assets: list[str] = field(default_factory=list)
    reference_evidence: list[dict[str, Any]] = field(default_factory=list)
    source_root: str = ""
    output_root: str = ""
    sample_fingerprint: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}
    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "VariantRecord":
        return cls(**{k: value[k] for k in cls.__dataclass_fields__ if k in value})

class HypitVariantStore:
    """Persist isolated branches and enforce sample-before-full approval."""
    def __init__(self, root: Path | str, *, reference_root: Path | str | None = None):
        self.root = _root(root, "variant root")
        self.root.mkdir(parents=True, exist_ok=True)
        self.reference_root = _root(reference_root, "reference root") if reference_root else self.root / "_reference"
        self.reference_root.mkdir(parents=True, exist_ok=True)
        self._reference_path = self.reference_root / "evidence.json"

    def _path(self, variant_id: str) -> Path:
        return self.root / variant_id / "variant.json"
    def _save(self, rec: VariantRecord) -> None:
        path = self._path(rec.variant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    def get(self, variant_id: str) -> VariantRecord:
        _id(variant_id, "variant_id")
        path = self._path(variant_id)
        if not path.is_file():
            raise VariantNotFoundError(variant_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            rec = VariantRecord.from_dict(value)
            if rec.variant_id != variant_id or rec.approval_state not in {"pending", "approved", "rejected"}:
                raise VariantError("malformed persisted variant record")
            for label, raw in (("brief", rec.brief), ("treatment", rec.treatment), ("analysis", rec.analysis), ("timeline", rec.timeline), ("sample_target", rec.sample_target), ("full_target", rec.full_target)):
                if not isinstance(raw, dict):
                    raise VariantError(f"malformed persisted {label}")
            for label in ("source_root", "output_root"):
                root = _root(getattr(rec, label), label)
                if root.is_file():
                    raise BranchOverwriteError(f"{label} is a file")
            return rec
        except BranchOverwriteError:
            raise
        except VariantError:
            raise
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise VariantError("malformed persisted variant record") from exc
    def list(self, *, profile_id: str | None = None, session_id: str | None = None) -> list[VariantRecord]:
        records = [self.get(p.parent.name) for p in self.root.glob("*/variant.json")]
        return [r for r in records if (profile_id is None or r.profile_id == profile_id) and (session_id is None or r.session_id == session_id)]

    def _reference(self, evidence: list[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
        values = [_json_value(dict(item), "reference evidence") for item in (evidence or [])]
        if not values and not self._reference_path.exists():
            return []
        encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        if self._reference_path.exists():
            existing = json.loads(self._reference_path.read_text(encoding="utf-8"))
            if existing.get("digest") != digest or existing.get("evidence") != values:
                raise VariantError("shared reference evidence is immutable")
        else:
            self._reference_path.write_text(json.dumps({"digest": digest, "evidence": values}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return values

    def create_variant(self, *, variant_id: str | None = None, profile_id: str, session_id: str,
                       parent_variant_id: str | None = None, brief: Mapping[str, Any],
                       treatment: Mapping[str, Any], analysis: Mapping[str, Any], timeline: Mapping[str, Any],
                       sample_target: Mapping[str, Any] | None = None, full_target: Mapping[str, Any] | None = None,
                       compare_metadata: Mapping[str, Any] | None = None, selected_anchors: list[str] | None = None,
                       selected_assets: list[str] | None = None, reference_evidence: list[Mapping[str, Any]] | None = None,
                       source_root: Path | str | None = None, output_root: Path | str | None = None) -> VariantRecord:
        variant_id = _id(variant_id or f"v-{uuid.uuid4().hex[:12]}", "variant_id")
        _text(profile_id, "profile_id", max_length=120); _text(session_id, "session_id", max_length=120)
        if self._path(variant_id).exists():
            raise VariantExistsError(variant_id)
        parent = None
        if parent_variant_id is not None:
            parent = _id(parent_variant_id, "parent_variant_id")
            parent_rec = self.get(parent)
            if (parent_rec.profile_id, parent_rec.session_id) != (profile_id, session_id):
                raise VariantError("parent variant is outside profile/session scope")
        src = _root(source_root or (self.root / "source" / variant_id), "source_root")
        out = _root(output_root or (self.root / "outputs" / variant_id), "output_root")
        if src == out or src in out.parents or out in src.parents:
            raise VariantError("source_root and output_root must be distinct")
        if src.exists() and src.is_file():
            raise BranchOverwriteError("source branch is a file")
        if out.exists() and out.is_file():
            raise BranchOverwriteError("output branch is a file")
        if out.exists() and any(out.iterdir()):
            raise BranchOverwriteError("output branch already contains artifacts")
        anchors = [_text(v, "selected anchor", max_length=500) for v in (selected_anchors or [])]
        assets = [_text(v, "selected asset", max_length=500) for v in (selected_assets or [])]
        evidence = self._reference(reference_evidence)
        rec = VariantRecord(variant_id, profile_id, session_id, parent,
            _json_value(dict(brief), "brief"), _json_value(dict(treatment), "treatment"),
            _json_value(dict(analysis), "analysis"), _json_value(dict(timeline), "timeline"),
            _json_value(dict(sample_target or {"kind": "sample", "duration_seconds": 15}), "sample target"),
            _json_value(dict(full_target or {"kind": "full"}), "full target"), compare_metadata=_json_value(dict(compare_metadata or {}), "compare metadata"),
            selected_anchors=anchors, selected_assets=assets, reference_evidence=evidence,
            source_root=str(src), output_root=str(out))
        self._save(rec)
        return rec

    @staticmethod
    def _current_sample_fingerprint(rec: VariantRecord) -> str:
        payload = {"sample_target": rec.sample_target, "reference_evidence": rec.reference_evidence}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def approve_sample(self, variant_id: str, *, sample_fingerprint: str | None = None,
                       approved_by: str = "user", metadata: Mapping[str, Any] | None = None) -> VariantRecord:
        rec = self.get(variant_id)
        _id(approved_by, "approved_by")
        fingerprint = self._current_sample_fingerprint(rec)
        if sample_fingerprint is not None and sample_fingerprint != fingerprint:
            raise ApprovalRequiredError("sample fingerprint does not match current target")
        rec.sample_fingerprint = _text(fingerprint, "sample_fingerprint", max_length=128)
        rec.approval_state = "approved"
        rec.approval = {"approved_by": approved_by, "sample_fingerprint": rec.sample_fingerprint, "metadata": _json_value(dict(metadata or {}), "approval metadata"), "approved_at": time.time()}
        rec.updated_at = time.time(); self._save(rec); return rec

    def reject_sample(self, variant_id: str, *, reason: str) -> VariantRecord:
        rec = self.get(variant_id); rec.approval_state = "rejected"; rec.approval = {"reason": _text(reason, "reason")}; rec.updated_at = time.time(); self._save(rec); return rec

    def assert_build_allowed(self, variant_id: str, *, target: str = "sample", sample_fingerprint: str | None = None) -> VariantRecord:
        if target not in {"sample", "full"}: raise VariantError("target must be sample or full")
        rec = self.get(variant_id)
        if target == "full":
            if rec.approval_state != "approved" or not rec.approval:
                raise ApprovalRequiredError("sample approval is required before full Build")
            current = self._current_sample_fingerprint(rec)
            if rec.sample_fingerprint != current:
                raise ApprovalRequiredError("stale sample approval")
            if sample_fingerprint is not None and sample_fingerprint != current:
                raise ApprovalRequiredError("stale sample approval")
        return rec

    def plan_build(self, variant_id: str, *, target: str = "sample", sample_fingerprint: str | None = None) -> dict[str, Any]:
        rec = self.assert_build_allowed(variant_id, target=target, sample_fingerprint=sample_fingerprint)
        branch = Path(rec.output_root) / target
        if branch.exists() and any(branch.iterdir()):
            raise BranchOverwriteError("build branch already contains artifacts")
        branch.mkdir(parents=True, exist_ok=True)
        return {"variant_id": rec.variant_id, "parent_variant_id": rec.parent_variant_id, "target": target,
                "source_root": rec.source_root, "output_root": str(branch), "approval_state": rec.approval_state}

    def compare(self, variant_ids: list[str] | None = None) -> dict[str, Any]:
        records = [self.get(v) for v in variant_ids] if variant_ids else self.list()
        return {"variants": [{"variant_id": r.variant_id, "parent_variant_id": r.parent_variant_id,
                              "approval_state": r.approval_state, "compare_metadata": r.compare_metadata,
                              "selected_anchors": list(r.selected_anchors), "selected_assets": list(r.selected_assets),
                              "sample_target": r.sample_target, "full_target": r.full_target} for r in records],
                "cost_scope": {"sample": sum(1 for r in records if r.sample_target), "full": sum(1 for r in records if r.full_target)}}

__all__ = ["HypitVariantStore", "VariantRecord", "VariantError", "VariantExistsError", "VariantNotFoundError", "BranchOverwriteError", "ApprovalRequiredError"]
