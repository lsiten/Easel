"""Crash-recoverable physical moves for the persona asset library."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal


class AssetMoveError(OSError):
    """A source file could not be moved without risking data loss."""


@dataclass(frozen=True, slots=True)
class MoveTicket:
    id: str
    root: Path
    source: Path
    target: Path
    staging: Path


@dataclass(frozen=True, slots=True)
class MoveJournal:
    id: str
    source: str
    target: str
    staging: str
    status: Literal["prepared", "moved", "committed", "rolled_back"]
    updated: float


def _journal_path(root: Path, identifier: str) -> Path:
    return root / ".moves" / f"{identifier}.json"


def _write_journal(root: Path, journal: MoveJournal) -> None:
    path = _journal_path(root, journal.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".move-", suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(asdict(journal), stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _contained(root: Path, target: Path) -> bool:
    try:
        Path(os.path.abspath(target)).relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _move(source: Path, target: Path, staging: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(source), str(staging))
        if target.is_symlink() and target.resolve() == source:
            target.unlink()
        if target.exists() or target.is_symlink():
            raise AssetMoveError(f"asset target already exists: {target}")
        os.replace(staging, target)
    except (OSError, shutil.Error):
        if not source.exists() and staging.exists():
            shutil.move(str(staging), str(source))
        staging.unlink(missing_ok=True)
        raise


def begin_move(root: Path, source: Path, target: Path) -> MoveTicket:
    """Move one file into the library and leave a journal until metadata commits."""
    if not source.is_file():
        raise AssetMoveError(f"asset source is missing: {source}")
    if not _contained(root, target):
        raise AssetMoveError(f"asset target escapes library: {target}")
    identifier = uuid.uuid4().hex
    staging = target.parent / f".{target.name}-{identifier}.move"
    ticket = MoveTicket(identifier, root, source, target, staging)
    journal = MoveJournal(identifier, str(source), str(target), str(staging), "prepared", time.time())
    _write_journal(root, journal)
    try:
        if source != target:
            _move(source, target, staging)
        _write_journal(root, MoveJournal(
            ticket.id, str(source), str(target), str(staging), "moved", time.time(),
        ))
    except (OSError, shutil.Error) as error:
        _restore(ticket)
        _write_journal(root, MoveJournal(
            ticket.id, str(source), str(target), str(staging), "rolled_back", time.time(),
        ))
        raise AssetMoveError(str(error)) from error
    return ticket


def commit_move(ticket: MoveTicket) -> None:
    _write_journal(ticket.root, MoveJournal(
        ticket.id, str(ticket.source), str(ticket.target), str(ticket.staging),
        "committed", time.time(),
    ))


def rollback_move(ticket: MoveTicket) -> None:
    _restore(ticket)
    _write_journal(ticket.root, MoveJournal(
        ticket.id, str(ticket.source), str(ticket.target), str(ticket.staging),
        "rolled_back", time.time(),
    ))


def _restore(ticket: MoveTicket) -> None:
    source, target, staging = ticket.source, ticket.target, ticket.staging
    if staging.is_file() and not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(source))
    if target.is_symlink():
        if target.resolve() == source:
            target.unlink(missing_ok=True)
        return
    if not target.is_file() or source.exists():
        return
    source.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(source))


def recover_moves(root: Path) -> None:
    """Finish committed journals or restore moves interrupted before index commit."""
    directory = root / ".moves"
    if not directory.is_dir():
        return
    try:
        index = json.loads((root / "_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = []
    committed_targets = {
        str(item.get("source", ""))
        for item in index
        if isinstance(item, dict)
    } if isinstance(index, list) else set()
    for path in directory.glob("*.json"):
        try:
            journal = MoveJournal(**json.loads(path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError):
            continue
        if journal.status in {"committed", "rolled_back"}:
            continue
        ticket = MoveTicket(
            journal.id, root, Path(journal.source), Path(journal.target), Path(journal.staging),
        )
        if journal.target in committed_targets:
            commit_move(ticket)
        else:
            rollback_move(ticket)
