"""Typed import/export boundary for Hypit and OpenFilm video artifacts.

Native engine files are retained under engine-specific namespaces. Only declared
and verified derived artifacts can cross into the shared output surface.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ARTIFACT_KINDS = frozenset({"mp4", "thumbnail", "brief", "report", "manifest"})
ENGINES = frozenset({"hypit", "openfilm"})
_RUNTIME_PARTS = frozenset({".hypit", ".svml", ".svs", ".svrun", "node_modules", ".git"})


class ArtifactBoundaryError(ValueError):
    """Raised when an artifact crosses an engine boundary unsafely."""


@dataclass(frozen=True)
class ArtifactRecord:
    kind: str
    path: str
    checksum: str
    source_engine: str
    target_profile: str
    attribution: str
    derived: bool = True

    def __post_init__(self) -> None:
        if self.kind not in ARTIFACT_KINDS:
            raise ArtifactBoundaryError(f"unsupported artifact kind: {self.kind}")
        if not isinstance(self.checksum, str) or len(self.checksum) != 64 or any(c not in "0123456789abcdef" for c in self.checksum):
            raise ArtifactBoundaryError("checksum must be a sha256 hex digest")
        if self.source_engine not in ENGINES:
            raise ArtifactBoundaryError("source_engine must be hypit or openfilm")
        if not _safe_scope(self.target_profile):
            raise ArtifactBoundaryError("invalid target_profile")
        if not isinstance(self.attribution, str) or not self.attribution.strip():
            raise ArtifactBoundaryError("attribution is required")
        if not self.derived:
            raise ArtifactBoundaryError("native artifacts cannot be exported as derived artifacts")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "checksum": self.checksum,
                "source_engine": self.source_engine, "target_profile": self.target_profile,
                "attribution": self.attribution, "derived": self.derived}


def _safe_scope(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 120 and not value.startswith((".", "_")) and not any(c in value for c in "/\\:\x00")


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ArtifactBoundaryError("artifact path must be a relative POSIX path")
    p = Path(value)
    if p.is_absolute() or any(part in {"", ".", ".."} for part in p.parts):
        raise ArtifactBoundaryError("artifact path escapes boundary")
    if any(part in _RUNTIME_PARTS or part.startswith(".") for part in p.parts):
        raise ArtifactBoundaryError("runtime or hidden files are not importable")
    return p.as_posix()


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _kind_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp4": return "mp4"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".avif"}: return "thumbnail"
    if suffix in {".md", ".txt"}: return "brief"
    if suffix in {".html", ".pdf", ".json"}:
        return "manifest" if path.name.endswith("manifest.json") else "report"
    raise ArtifactBoundaryError(f"unsupported artifact extension: {suffix}")


def _validate_entry(entry: Mapping[str, Any], *, source_engine: str, target_profile: str) -> tuple[str, str, str]:
    if not isinstance(entry, Mapping):
        raise ArtifactBoundaryError("artifact entry must be an object")
    kind = entry.get("kind")
    path = _safe_relative(entry.get("path"))
    if kind not in ARTIFACT_KINDS:
        raise ArtifactBoundaryError(f"unsupported artifact kind: {kind}")
    if kind == "manifest" and path.endswith("film.html"):
        raise ArtifactBoundaryError("implicit Hypit to film.html conversion is forbidden")
    if entry.get("source_engine", source_engine) != source_engine:
        raise ArtifactBoundaryError("source engine mismatch")
    if entry.get("target_profile", target_profile) != target_profile:
        raise ArtifactBoundaryError("target profile mismatch")
    attribution = entry.get("attribution")
    if not isinstance(attribution, str) or not attribution.strip():
        raise ArtifactBoundaryError("attribution is required")
    checksum = entry.get("checksum")
    if not isinstance(checksum, str) or len(checksum) != 64:
        raise ArtifactBoundaryError("checksum is required")
    return kind, path, checksum


def import_artifacts(*, source_root: Path | str, destination_root: Path | str, source_engine: str,
                     target_profile: str, manifest: Mapping[str, Any] | Path | str) -> list[ArtifactRecord]:
    """Import declared derived artifacts into a profile-scoped destination."""
    if source_engine not in ENGINES or not _safe_scope(target_profile):
        raise ArtifactBoundaryError("invalid engine or target profile")
    src_root, dest_root = Path(source_root).resolve(), Path(destination_root).resolve()
    if isinstance(manifest, (str, Path)):
        try: data = json.loads(Path(manifest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise ArtifactBoundaryError("malformed artifact manifest") from exc
    else: data = manifest
    entries = data.get("artifacts") if isinstance(data, Mapping) else None
    if not isinstance(entries, list):
        raise ArtifactBoundaryError("manifest artifacts must be a list")
    records: list[ArtifactRecord] = []
    staged: list[tuple[Path, Path]] = []
    try:
        for entry in entries:
            kind, rel, expected = _validate_entry(entry, source_engine=source_engine, target_profile=target_profile)
            src = (src_root / rel).resolve()
            if not src.is_file() or not src.is_relative_to(src_root):
                raise ArtifactBoundaryError("artifact source is missing or escapes source root")
            actual = _checksum(src)
            if actual != expected:
                raise ArtifactBoundaryError("artifact checksum mismatch")
            out_rel = Path("derived") / target_profile / kind / Path(rel).name
            dst = (dest_root / out_rel).resolve()
            if not dst.is_relative_to(dest_root): raise ArtifactBoundaryError("destination escapes root")
            if dst.exists(): raise ArtifactBoundaryError("refusing to overwrite existing artifact")
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkstemp(prefix=".artifact-", dir=dst.parent)[1])
            shutil.copy2(src, tmp)
            staged.append((tmp, dst))
            records.append(ArtifactRecord(kind, out_rel.as_posix(), actual, source_engine, target_profile, str(entry["attribution"])))
        for tmp, dst in staged: os.replace(tmp, dst)
        return records
    except Exception:
        for tmp, _ in staged: tmp.unlink(missing_ok=True)
        for _, dst in staged: dst.unlink(missing_ok=True)
        raise


def export_artifacts(*, source_root: Path | str, destination_root: Path | str, source_engine: str,
                     target_profile: str, records: list[ArtifactRecord] | tuple[ArtifactRecord, ...]) -> list[ArtifactRecord]:
    """Export only verified derived records, preserving engine namespaces."""
    if source_engine not in ENGINES or not _safe_scope(target_profile): raise ArtifactBoundaryError("invalid engine or target profile")
    src_root, dst_root = Path(source_root).resolve(), Path(destination_root).resolve()
    result: list[ArtifactRecord] = []
    for record in records:
        if not isinstance(record, ArtifactRecord) or record.source_engine != source_engine or record.target_profile != target_profile:
            raise ArtifactBoundaryError("record provenance mismatch")
        rel = _safe_relative(record.path)
        src = (src_root / rel).resolve()
        if not src.is_file() or not src.is_relative_to(src_root): raise ArtifactBoundaryError("artifact path escapes source root")
        if _checksum(src) != record.checksum: raise ArtifactBoundaryError("artifact checksum mismatch")
        dst = (dst_root / rel).resolve()
        if dst.exists(): raise ArtifactBoundaryError("refusing to overwrite existing artifact")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        result.append(record)
    return result


def import_native_sources(*, source_root: Path | str, destination_root: Path | str,
                          source_engine: str, target_profile: str,
                          sources: Mapping[str, Path | str]) -> dict[str, str]:
    """Copy native project/source/run trees into an engine-specific namespace."""
    if source_engine not in ENGINES or not _safe_scope(target_profile):
        raise ArtifactBoundaryError("invalid engine or target profile")
    allowed = {"project", "source", "run"}
    if not isinstance(sources, Mapping) or set(sources) - allowed:
        raise ArtifactBoundaryError("native sources must be project, source, or run")
    src_root, dst_root = Path(source_root).resolve(), Path(destination_root).resolve()
    result: dict[str, str] = {}
    staged: list[tuple[Path, Path]] = []
    try:
        for name, raw in sources.items():
            rel = _safe_relative(str(raw))
            src = (src_root / rel).resolve()
            if not src.is_dir() or not src.is_relative_to(src_root):
                raise ArtifactBoundaryError("native source is missing or escapes source root")
            dst = (dst_root / "engines" / source_engine / target_profile / "native" / name).resolve()
            if dst.exists(): raise ArtifactBoundaryError("refusing to overwrite native namespace")
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(prefix=".native-", dir=dst.parent))
            shutil.copytree(src, tmp / name)
            staged.append((tmp / name, dst))
            result[name] = dst.relative_to(dst_root).as_posix()
        for tmp, dst in staged: os.replace(tmp, dst)
        return result
    except Exception:
        for tmp, dst in staged:
            shutil.rmtree(tmp, ignore_errors=True)
            dst.unlink(missing_ok=True)
        raise
