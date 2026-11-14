"""Transactional physical project moves with reference rewriting and rollback."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from easel.project_organization_models import ProjectMove
from easel.project_reference_rewrite import TEXT_SUFFIXES, rewrite_references
from skills.shared.scripts.asset_storage import _storage_lock


class JournalMove(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: str
    sha256: str
    post_sha256: str = ""


class TransactionJournal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    profile: str
    project: str
    status: Literal["prepared", "moving", "rewriting", "committed", "rolling_back", "rolled_back"] = "prepared"
    moves: tuple[JournalMove, ...]
    backups: tuple[str, ...] = ()
    created: float
    updated: float
    error: str = ""


class ProjectTransactionError(ValueError):
    """A project move failed a path, hash, or transaction invariant."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _write_json(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".organize-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(value.model_dump_json(indent=2) + "\n")
    os.replace(temporary, path)


def _safe(project: Path, relative: str) -> Path:
    candidate = (project / relative).resolve()
    root = project.resolve()
    if candidate == root or not candidate.is_relative_to(root):
        raise ProjectTransactionError("project path escapes its root")
    return candidate


def _backup_text(project: Path, journal_dir: Path, relative: str) -> None:
    source = _safe(project, relative)
    if not source.is_file() or source.suffix.lower() not in TEXT_SUFFIXES:
        return
    destination = journal_dir / "backups" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _update_manifest(project: Path, profile: str, moves: tuple[ProjectMove, ...]) -> None:
    path = project / ".easel.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {"topic": project.name, "profile": profile, "steps": []}
    value["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    value["organization"] = {
        "schemaVersion": 1,
        "organizedAt": value["updated"],
        "files": [
            {"path": move.target, "source": move.source, "role": move.role.value,
             "confidence": move.confidence, "reason": move.reason,
             "sha256": _digest(_safe(project, move.target))}
            for move in moves
        ],
    }
    temporary = project / ".easel.json.organize.tmp"
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _rollback(journal: TransactionJournal, project: Path, journal_dir: Path,
              *, verify: bool) -> None:
    for move in reversed(journal.moves):
        source = _safe(project, move.source)
        target = _safe(project, move.target)
        if source.as_posix() == target.as_posix() or not target.exists():
            continue
        if verify and move.post_sha256 and _digest(target) != move.post_sha256:
            raise ProjectTransactionError(f"project changed after organization: {move.target}")
        source.parent.mkdir(parents=True, exist_ok=True)
        os.replace(target, source)
    for relative in journal.backups:
        backup = journal_dir / "backups" / relative
        if backup.is_file():
            destination = _safe(project, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination)


def _prune_empty_sources(project: Path, moves: tuple[ProjectMove, ...]) -> None:
    """Remove source directories emptied by this transaction, never the project root."""
    root = project.resolve()
    candidates = {
        (_safe(project, move.source).parent)
        for move in moves if move.source != move.target
    }
    for directory in sorted(candidates, key=lambda path: len(path.parts), reverse=True):
        current = directory
        while current != root and current.is_relative_to(root):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def _record_status(path: Path, journal: TransactionJournal, status: str,
                   *, error: str = "") -> TransactionJournal:
    updated = journal.model_copy(update={"status": status, "updated": time.time(), "error": error})
    _write_json(path, updated)
    return updated


def apply_project_plan(outputs: Path, project: Path, profile: str,
                       moves: tuple[ProjectMove, ...]) -> str:
    """Move files, rewrite references, and commit only after validation."""
    for move in moves:
        source = _safe(project, move.source)
        if not source.is_file():
            raise ProjectTransactionError(f"project file disappeared: {move.source}")
        if _digest(source) != move.sha256:
            raise ProjectTransactionError(f"project file changed: {move.source}")
    identifier = uuid.uuid4().hex
    journal_dir = outputs / "_organize" / identifier
    journal_dir.mkdir(parents=True, exist_ok=False)
    journal = TransactionJournal(id=identifier, profile=profile, project=project.name,
                                 moves=tuple(JournalMove(source=move.source, target=move.target,
                                                         sha256=move.sha256) for move in moves),
                                 created=time.time(), updated=time.time())
    journal_path = journal_dir / "journal.json"
    backups = {
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
        and not any(part.startswith(".") for part in path.relative_to(project).parts[:-1])
    } | {".easel.json", "film.html"}
    for relative in sorted(backups):
        _backup_text(project, journal_dir, relative)
    journal = journal.model_copy(update={"backups": tuple(sorted(backups))})
    _write_json(journal_path, journal)
    with _storage_lock(outputs / "_organize" / ".lock"):
        try:
            journal = _record_status(journal_path, journal, "moving")
            for move in moves:
                if move.source == move.target:
                    continue
                source, target = _safe(project, move.source), _safe(project, move.target)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
            _prune_empty_sources(project, moves)
            journal = _record_status(journal_path, journal, "rewriting")
            rewrite_references(project, moves)
            _update_manifest(project, profile, moves)
            updated_moves = []
            for move in journal.moves:
                target = _safe(project, move.target)
                updated_moves.append(move.model_copy(update={"post_sha256": _digest(target)}))
            journal = journal.model_copy(update={"moves": tuple(updated_moves)})
            _write_json(journal_path, journal)
            journal = _record_status(journal_path, journal, "committed")
            return journal.id
        except (OSError, ValueError, json.JSONDecodeError) as error:
            _rollback(journal, project, journal_dir, verify=False)
            _record_status(journal_path, journal, "rolled_back", error=str(error))
            raise


def rollback_transaction(outputs: Path, transaction_id: str) -> None:
    """Restore a completed transaction only when touched files are unchanged."""
    journal_dir = outputs / "_organize" / transaction_id
    journal_path = journal_dir / "journal.json"
    try:
        journal = TransactionJournal.model_validate_json(journal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProjectTransactionError("organization transaction not found") from error
    if journal.status == "rolled_back":
        return
    project = outputs / journal.project
    with _storage_lock(outputs / "_organize" / ".lock"):
        journal = _record_status(journal_path, journal, "rolling_back")
        try:
            _rollback(journal, project, journal_dir, verify=True)
        except (OSError, ValueError) as error:
            _record_status(journal_path, journal, "committed", error=str(error))
            raise
        _record_status(journal_path, journal, "rolled_back")


def recover_interrupted(outputs: Path) -> None:
    """Roll back journals left mid-flight by a process restart."""
    root = outputs / "_organize"
    if not root.is_dir():
        return
    for journal_path in root.glob("*/journal.json"):
        try:
            journal = TransactionJournal.model_validate_json(journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if journal.status not in {"prepared", "moving", "rewriting"}:
            continue
        journal_dir = journal_path.parent
        project = outputs / journal.project
        try:
            _rollback(journal, project, journal_dir, verify=False)
            _record_status(journal_path, journal, "rolled_back", error="service restarted")
        except OSError:
            continue
