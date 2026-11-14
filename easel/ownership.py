"""Persona ownership and media manifest checks for write paths.

The output library is shared on disk, so a request persona must be proved by the
project manifest before a file can be exported or submitted.  This module keeps
that policy independent from the HTTP layer and publisher adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from easel.persona import valid_persona_name


class OwnershipError(ValueError):
    """Raised when a path or project cannot be proven to belong to a persona."""


@dataclass(frozen=True, slots=True)
class MediaManifestEntry:
    """Stable identity of one media input used by a publish run."""

    relative_path: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    """Validated project owner and manifest path."""

    project: Path
    persona: str
    manifest: Path


def validate_persona(persona: str) -> str:
    """Validate a named persona and require that its profile exists."""
    value = persona.strip()
    if not value or not valid_persona_name(value):
        raise OwnershipError("无效画像")
    return value


def _manifest_data(project: Path) -> Mapping[str, Any]:
    manifest = project / ".easel.json"
    if manifest.is_symlink():
        raise OwnershipError("项目元数据不能是符号链接")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipError("项目元数据不可读") from exc
    if not isinstance(value, dict):
        raise OwnershipError("项目元数据格式无效")
    return value


def project_identity(outputs: Path, relative_project: str, persona: str) -> ProjectIdentity:
    """Resolve a project directory and prove its explicit manifest owner."""
    owner = validate_persona(persona)
    root = outputs.resolve()
    candidate = (root / relative_project).resolve()
    if root not in candidate.parents or not candidate.is_dir():
        raise OwnershipError("项目路径超出内容库")
    if any(part.startswith("_") or part.startswith(".") for part in candidate.relative_to(root).parts):
        raise OwnershipError("系统目录或隐藏目录不可作为项目")
    data = _manifest_data(candidate)
    if data.get("profile") != owner:
        raise OwnershipError("项目不属于当前画像")
    return ProjectIdentity(project=candidate, persona=owner, manifest=candidate / ".easel.json")


def owned_media_path(outputs: Path, persona: str, relative_path: str) -> tuple[ProjectIdentity, Path]:
    """Resolve one media path and prove the containing project owner."""
    if not relative_path or Path(relative_path).is_absolute():
        raise OwnershipError("媒体路径必须是内容库相对路径")
    root = outputs.resolve()
    media = (root / relative_path).resolve()
    if root not in media.parents or not media.is_file():
        raise OwnershipError("媒体路径超出内容库或文件不存在")
    if (root / relative_path).is_symlink():
        raise OwnershipError("媒体路径不能是符号链接")
    rel = media.relative_to(root)
    if len(rel.parts) < 2 or any(part.startswith("_") or part.startswith(".") for part in rel.parts):
        raise OwnershipError("不能使用系统目录或隐藏文件")
    project = project_identity(root, rel.parts[0], persona)
    if project.project not in media.parents:
        raise OwnershipError("媒体不属于项目目录")
    return project, media


def build_media_manifest(outputs: Path, persona: str, media: tuple[str, ...] | list[str]) -> tuple[MediaManifestEntry, ...]:
    """Validate media ownership and hash immutable inputs for approval/idempotency."""
    entries: list[MediaManifestEntry] = []
    seen: set[str] = set()
    for relative in media:
        project, path = owned_media_path(outputs, persona, relative)
        del project
        canonical = path.relative_to(outputs.resolve()).as_posix()
        if canonical in seen:
            continue
        seen.add(canonical)
        with path.open('rb') as stream:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        entries.append(MediaManifestEntry(canonical, digest, path.stat().st_size))
    return tuple(entries)


def freeze_media(outputs: Path, persona: str, run_id: str,
                 manifest: tuple[MediaManifestEntry, ...]) -> tuple[Path, ...]:
    """Freeze exactly the approved bytes before giving media to an external runner."""
    import re
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise OwnershipError('非法发布 run_id')
    destination = outputs / '_publish' / persona / run_id / 'media'
    if not destination.resolve().is_relative_to(outputs.resolve()) or destination.is_symlink():
        raise OwnershipError('发布媒体目录越界')
    destination.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    for index, item in enumerate(manifest):
        _identity, source = owned_media_path(outputs, persona, item.relative_path)
        target = destination / f'{index:02d}{source.suffix.lower()}'
        descriptor, temporary_name = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=destination)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, 'wb') as writer, source.open('rb') as reader:
                for chunk in iter(lambda: reader.read(1024 * 1024), b''):
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            if digest.hexdigest() != item.sha256 or temporary.stat().st_size != item.size:
                raise OwnershipError('媒体内容已改变，请重新审核')
            os.replace(temporary, target)
            target.chmod(0o400)
        finally:
            temporary.unlink(missing_ok=True)
        staged.append(target)
    return tuple(staged)
