"""Collision-safe, source-addressed storage for the per-persona asset library."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import NotRequired, TypedDict

try:
    from asset_move import commit_move, recover_moves, rollback_move, begin_move
except ModuleNotFoundError:
    from skills.shared.scripts.asset_move import commit_move, recover_moves, rollback_move, begin_move

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class AssetEntry(TypedDict):
    name: str
    kind: str
    category: str
    tags: list[str]
    summary: str
    source: str
    original_source: NotRequired[str]
    mtime: int
    stored_name: str
    sha256: NotRequired[str]
    capturedAt: NotRequired[str]
    location: NotRequired[str]
    timeSource: NotRequired[str]
    project: NotRequired[str]
    role: NotRequired[str]
    confidence: NotRequired[float]
    reason: NotRequired[str]
    manual: NotRequired[bool]


class StorageMetadata(TypedDict):
    sources: list[str]
    organized_at: str
    index: str


class UnsafeCategory(ValueError):
    def __init__(self, category: str) -> None:
        super().__init__(f"非法素材分类路径：{category!r}")
        self.category = category


def category_directory(root: Path, category: str) -> Path:
    parts = category.split("/")
    if (Path(category).is_absolute() or any(part in {"", ".", ".."} for part in parts)
            or "\\" in category or any(ord(char) < 32 for char in category)):
        raise UnsafeCategory(category)
    if root.is_symlink() or root.parent.is_symlink():
        raise UnsafeCategory(category)
    directory = root
    for part in parts:
        directory = directory / part
        if directory.is_symlink():
            raise UnsafeCategory(category)
    if not directory.resolve().is_relative_to(root.resolve()):
        raise UnsafeCategory(category)
    return directory


def read_index(root: Path) -> list[AssetEntry]:
    try:
        data = json.loads((root / "_index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    entries: list[AssetEntry] = []
    for item in data:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str)
                                               for key in ("name", "category", "source")):
            continue
        entry: AssetEntry = {
            "name": item["name"], "category": item["category"], "source": item["source"],
            "kind": str(item.get("kind", "other")), "summary": str(item.get("summary", "")),
            "tags": [tag for tag in item.get("tags", []) if isinstance(tag, str)],
            "mtime": item.get("mtime", 0), "stored_name": item.get("stored_name", item["name"]),
        }
        if isinstance(item.get("sha256"), str):
            entry["sha256"] = item["sha256"]
        if isinstance(item.get("original_source"), str):
            entry["original_source"] = item["original_source"]
        if isinstance(item.get("capturedAt"), str):
            entry["capturedAt"] = item["capturedAt"]
        if isinstance(item.get("location"), str):
            entry["location"] = item["location"]
        if isinstance(item.get("timeSource"), str):
            entry["timeSource"] = item["timeSource"]
        if isinstance(item.get("project"), str):
            entry["project"] = item["project"]
        if isinstance(item.get("role"), str):
            entry["role"] = item["role"]
        if isinstance(item.get("confidence"), (int, float)):
            entry["confidence"] = float(item["confidence"])
        if isinstance(item.get("reason"), str):
            entry["reason"] = item["reason"]
        if isinstance(item.get("manual"), bool):
            entry["manual"] = item["manual"]
        entries.append(entry)
    return entries


def _write_json(path: Path, entries: list[AssetEntry] | StorageMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(entries, stream, ensure_ascii=False, indent=2)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _destination(directory: Path, entry: AssetEntry) -> Path:
    source = Path(entry["source"])
    name = entry["name"]
    digest = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    candidate = directory / name
    attempt = 0
    while (candidate.name in {"manifest.json", "_index.json", "_meta.json"}
           or candidate.exists() or candidate.is_symlink()):
        if ((candidate.is_file() or candidate.is_symlink())
                and candidate.resolve() == source.resolve()):
            return candidate
        suffix = f"-{attempt}" if attempt else ""
        candidate = directory / f"{Path(name).stem}-{digest}{suffix}{Path(name).suffix}"
        attempt += 1
    return candidate


@contextmanager
def _storage_lock(path: Path) -> Iterator[None]:
    """Serialize writers with a native advisory lock, released even on failure."""
    with path.open("a+b") as lock:
        if os.name == "nt":
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def store_entry(root: Path, entry: AssetEntry) -> tuple[Path, AssetEntry]:
    """Commit one source update under a process lock, rolling back failed metadata writes."""
    directory = category_directory(root, entry["category"])
    root.mkdir(parents=True, exist_ok=True)
    if (root / ".storage.lock").is_symlink() or (root / "_index.json").is_symlink():
        raise UnsafeCategory(entry["category"])
    with _storage_lock(root / ".storage.lock"):
        recover_moves(root)
        previous = read_index(root)
        source = Path(entry["source"]).resolve()
        old = [item for item in previous if Path(item["source"]).resolve() == source]
        retained = [item for item in previous if Path(item["source"]).resolve() != source]
        destination = _destination(directory, entry)
        origin = entry.get("original_source")
        if not origin:
            origin = old[0].get("original_source", str(source)) if old else str(source)
        stored = {
            **entry,
            "source": os.path.abspath(destination),
            "original_source": origin,
            "stored_name": destination.name,
        }
        updated = [*retained, stored]
        categories = {item["category"] for item in old} | {entry["category"]}
        manifests = {category_directory(root, category) / "manifest.json":
                     [item for item in updated if item["category"] == category] for category in categories}
        writes = {**manifests, root / "_index.json": updated}
        if any(path.is_symlink() for path in writes):
            raise UnsafeCategory(entry["category"])
        snapshots = {path: path.read_bytes() if path.exists() else None for path in writes}
        directory.mkdir(parents=True, exist_ok=True)
        ticket = None
        try:
            ticket = begin_move(root, source, destination)
            for path, items in writes.items():
                _write_json(path, items)
            commit_move(ticket)
        except (OSError, ValueError):
            if ticket is not None:
                rollback_move(ticket)
            for path, content in snapshots.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            raise
        for item in old:
            old_name = item["stored_name"]
            if Path(old_name).name != old_name:
                continue
            old_path = category_directory(root, item["category"]) / old_name
            if old_path == destination or old_path == source:
                continue
            if old_path.is_symlink() and old_path.resolve() == source:
                old_path.unlink()
            elif old_path.is_file() and old_path.resolve() == source:
                old_path.unlink()
        return destination, stored
