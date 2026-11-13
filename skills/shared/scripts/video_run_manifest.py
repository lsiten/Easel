"""Versioned, engine-scoped manifests for video runs.

The manifest is deliberately independent from OpenFilm's ``.easel.json``.  It
contains only run metadata and paths relative to the assigned project root.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

MANIFEST_VERSION = 1
SUPPORTED_ENGINES = frozenset({"openfilm", "hypit"})
STATES = frozenset({"planned", "running", "paused", "succeeded", "failed", "cancelled"})
_TRANSITIONS = {
    "planned": {"running", "cancelled"},
    "running": {"paused", "succeeded", "failed", "cancelled"},
    "paused": {"running", "cancelled"},
    "succeeded": set(),
    "failed": {"running", "cancelled"},
    "cancelled": set(),
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ManifestError(ValueError):
    """Raised when a run manifest violates its schema or scope."""


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ManifestError(f"invalid {label}")
    return value


def _scope(value: Any, label: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 120
            or value.startswith((".", "_"))
            or any(c in value for c in ("/", "\\", ":"))
            or any(ord(c) < 32 for c in value)):
        raise ManifestError(f"invalid {label}")
    return value


def _relative_path(value: Any, root: Path, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ManifestError(f"invalid {label}")
    candidate = (root / value).resolve()
    try:
        rel = candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ManifestError(f"{label} escapes project root") from exc
    if not rel.parts or any(part in {".", ".."} for part in Path(value).parts):
        raise ManifestError(f"invalid {label}")
    return rel.as_posix()


def _path_list(values: Any, root: Path, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ManifestError(f"{label} must be a list")
    if len(values) != len({item for item in values if isinstance(item, str)}):
        raise ManifestError(f"{label} must contain unique paths")
    return [_relative_path(item, root, label=label) for item in values]


def _validate_project_scope(root: Path, profile_id: str, session_id: str) -> None:
    """Require a project root produced by the profile/session resolver."""
    expected = ("profiles", profile_id, "sessions", session_id, "video")
    parts = root.parts
    if len(parts) < len(expected) + 1 or tuple(parts[-len(expected)-1:-1]) != expected:
        raise ManifestError("project_root does not match profile/session scope")


@dataclass(frozen=True)
class VideoRunManifest:
    run_id: str
    attempt_id: str
    build_id: str
    engine: str
    profile_id: str
    session_id: str
    project_root: str
    state: str = "planned"
    migration_version: int = MANIFEST_VERSION
    checkpoint: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    provider_policy: dict[str, Any] = field(default_factory=dict)
    schema_version: ClassVar[int] = MANIFEST_VERSION

    def __post_init__(self) -> None:
        root = Path(self.project_root).expanduser().resolve()
        _id(self.run_id, "run_id")
        _id(self.attempt_id, "attempt_id")
        _id(self.build_id, "build_id")
        if len({self.run_id, self.attempt_id, self.build_id}) != 3:
            raise ManifestError("run, attempt and build IDs must be distinct")
        if not isinstance(self.engine, str) or self.engine not in SUPPORTED_ENGINES:
            raise ManifestError(f"unknown engine: {self.engine!r}")
        _scope(self.profile_id, "profile_id")
        _scope(self.session_id, "session_id")
        _validate_project_scope(root, self.profile_id, self.session_id)
        if not isinstance(self.migration_version, int) or self.migration_version != MANIFEST_VERSION:
            raise ManifestError("unsupported migration_version")
        if self.state not in STATES:
            raise ManifestError(f"invalid state: {self.state}")
        if (not isinstance(self.checkpoint, dict) or not isinstance(self.provenance, dict)
                or not isinstance(self.provider_policy, dict)):
            raise ManifestError("checkpoint, provenance and provider_policy must be objects")
        _path_list(self.outputs, root, "outputs")
        _path_list(self.reports, root, "reports")
        # Native Hypit runtime internals never become generic output/report paths.
        for path in [*self.outputs, *self.reports]:
            if any(part in {".hypit", ".svml", ".svs", ".svrun"} for part in Path(path).parts):
                raise ManifestError("Hypit runtime files are not publishable artifacts")

    @classmethod
    def create(cls, *, run_id: str, attempt_id: str, build_id: str, engine: str,
               profile_id: str, session_id: str, project_root: Path | str,
               state: str = "planned", **kwargs: Any) -> "VideoRunManifest":
        return cls(run_id=run_id, attempt_id=attempt_id, build_id=build_id, engine=engine,
                   profile_id=profile_id, session_id=session_id,
                   project_root=str(Path(project_root).expanduser().resolve()), state=state, **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, project_root: Path | str | None = None) -> "VideoRunManifest":
        if not isinstance(data, dict):
            raise ManifestError("manifest must be an object")
        required = {"run_id", "attempt_id", "build_id", "engine", "profile_id", "session_id", "project_root"}
        missing = required - data.keys()
        if missing:
            raise ManifestError(f"missing fields: {', '.join(sorted(missing))}")
        if not isinstance(data.get("project_root"), str):
            raise ManifestError("project_root must be a string")
        root = Path(project_root or data["project_root"]).expanduser().resolve()
        raw_root = Path(data["project_root"]).expanduser().resolve()
        if raw_root != root:
            raise ManifestError("project_root does not match assigned root")
        allowed = {"schema_version", "migration_version", "run_id", "attempt_id", "build_id", "engine",
                   "profile_id", "session_id", "project_root", "state", "checkpoint", "outputs", "reports", "provenance", "provider_policy"}
        unknown = set(data) - allowed
        if unknown:
            raise ManifestError(f"unknown fields: {', '.join(sorted(unknown))}")
        schema_version = data.get("schema_version", MANIFEST_VERSION)
        migration_version = data.get("migration_version", schema_version)
        if (not isinstance(schema_version, int) or schema_version != MANIFEST_VERSION
                or not isinstance(migration_version, int)
                or migration_version != schema_version):
            raise ManifestError("unsupported or mismatched schema/migration version")
        return cls(run_id=data["run_id"], attempt_id=data["attempt_id"], build_id=data["build_id"],
                   engine=data["engine"], profile_id=data["profile_id"], session_id=data["session_id"],
                   project_root=str(root), state=data.get("state", "planned"),
                   migration_version=migration_version,
                   checkpoint=data.get("checkpoint", {}), outputs=data.get("outputs", []),
                   reports=data.get("reports", []), provenance=data.get("provenance", {}),
                   provider_policy=data.get("provider_policy", {}))

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "migration_version": self.migration_version,
                "run_id": self.run_id, "attempt_id": self.attempt_id, "build_id": self.build_id,
                "engine": self.engine, "profile_id": self.profile_id, "session_id": self.session_id,
                "project_root": self.project_root, "state": self.state, "checkpoint": self.checkpoint,
                "outputs": list(self.outputs), "reports": list(self.reports), "provenance": self.provenance,
                "provider_policy": self.provider_policy}

    def transition(self, state: str) -> "VideoRunManifest":
        if state not in _TRANSITIONS.get(self.state, set()):
            raise ManifestError(f"invalid state transition: {self.state} -> {state}")
        return VideoRunManifest.from_dict({**self.to_dict(), "state": state})

    @classmethod
    def read(cls, path: Path | str, *, project_root: Path | str | None = None) -> "VideoRunManifest":
        target = Path(path)
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"cannot read manifest: {target}") from exc
        return cls.from_dict(data, project_root=project_root)

    def write(self, path: Path | str) -> Path:
        target = Path(path)
        # The manifest itself must live under the assigned project root.
        _relative_path(str(target), Path(self.project_root), label="manifest path")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.to_dict(), stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target


def resolve_project_root(root: Path | str, profile_id: str, session_id: str, run_id: str) -> Path:
    """Return an isolated per-profile/session/run directory under ``root``."""
    base = Path(root).expanduser().resolve()
    _scope(profile_id, "profile_id")
    _scope(session_id, "session_id")
    _id(run_id, "run_id")
    target = (base / "profiles" / profile_id / "sessions" / session_id / "video" / run_id).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ManifestError("project root escapes workspace")
    target.mkdir(parents=True, exist_ok=True)
    return target


def read_openfilm_session_pointer(root: Path | str, session_id: str) -> Path | None:
    """Read the legacy OpenFilm pointer without allowing output-root traversal."""
    base = Path(root).expanduser().resolve()
    _scope(session_id, "session_id")
    pointer = base / "outputs" / "_sessions" / f"{session_id}.openfilm-topic.txt"
    try:
        topic = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not topic:
        return None
    outputs = (base / "outputs").resolve()
    candidate = (outputs / topic).resolve() if not Path(topic).is_absolute() else Path(topic).resolve()
    if candidate == outputs or not candidate.is_relative_to(outputs):
        return None
    return candidate if (candidate / "film.html").is_file() else None


def is_public_artifact(path: Path | str) -> bool:
    """Generic asset/content APIs may expose derived media only."""
    parts = Path(path).parts
    return not any(part in {".hypit", ".svml", ".svs", ".svrun"} for part in parts)
