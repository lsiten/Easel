"""Append-only account and work snapshots with explicit provenance."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import JsonValue
from skills.shared.scripts.asset_storage import _storage_lock


class SnapshotError(ValueError):
    """Invalid snapshot identity or storage boundary."""


MetricValue = int | float | str | None


@dataclass(frozen=True, slots=True)
class WorkSnapshot:
    platform_work_id: str
    permalink: str
    title: str
    metrics: tuple[tuple[str, MetricValue], ...]


@dataclass(frozen=True, slots=True)
class AnalyticsSnapshot:
    persona: str
    platform: str
    observed_at: str
    logged_in: bool
    account_metrics: tuple[tuple[str, MetricValue], ...]
    content_metrics: tuple[tuple[str, MetricValue], ...]
    source: str
    snapshot_id: str
    account_uid: str | None = None
    timezone: str = 'UTC'
    raw_payload_hash: str = ''
    works: tuple[WorkSnapshot, ...] = ()
    schema_version: int = 2

    def to_dict(self) -> dict[str, JsonValue]:
        value = asdict(self)
        value['account_metrics'] = dict(self.account_metrics)
        value['content_metrics'] = dict(self.content_metrics)
        return value


def _safe_segment(value: str, field: str) -> str:
    text = value.strip()
    if not text or text.startswith('.') or re.search(r'[\\/\x00-\x1f]', text):
        raise SnapshotError(f'非法{field}')
    return text


def snapshot_path(root: Path, persona: str, platform: str) -> Path:
    boundary = root.resolve()
    path = boundary / '_analytics' / 'snapshots' / _safe_segment(persona, '画像') / f'{_safe_segment(platform, "平台")}.jsonl'
    if any(component.is_symlink() for component in (path, *path.parents) if component != boundary) or not path.resolve().is_relative_to(boundary):
        raise SnapshotError('快照目录越界')
    return path


def _metric_pairs(value: JsonValue) -> tuple[tuple[str, MetricValue], ...]:
    match value:
        case dict():
            return tuple(sorted((key, item) for key, item in value.items()
                                if item is None or type(item) in (int, float, str)))
        case list():
            return tuple((str(item['label']), item.get('value')) for item in value
                         if isinstance(item, dict) and isinstance(item.get('label'), str)
                         and (item.get('value') is None or type(item.get('value')) in (int, float, str)))
        case _:
            return ()


def normalize_snapshot(persona: str, platform: str, payload: Mapping[str, JsonValue], *,
                       observed_at: str | None = None, source: str = 'account_stats') -> AnalyticsSnapshot:
    owner, name = _safe_segment(persona, '画像'), _safe_segment(platform, '平台')
    fetched = payload.get('fetched_at')
    stamp = observed_at or (datetime.fromtimestamp(fetched, timezone.utc).isoformat()
                           if type(fetched) in (int, float) else datetime.now(timezone.utc).isoformat())
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('missing timezone')
    except ValueError as error:
        raise SnapshotError('observed_at 必须包含 ISO-8601 时区') from error
    account = dict(_metric_pairs(payload.get('account_metrics', payload.get('overview'))))
    for key in ('followers', 'likes', 'posts', 'following'):
        value = payload.get(key)
        if value is None or type(value) in (int, float, str):
            account[key] = value
    raw_hash = hashlib.sha256(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    uid = payload.get('account_uid') or payload.get('accountUid') or payload.get('userId')
    account_uid = str(uid) if type(uid) in (str, int) and str(uid).strip() else None
    works: list[WorkSnapshot] = []
    notes = payload.get('notes', [])
    if isinstance(notes, list):
        for note in notes:
            if not isinstance(note, dict):
                continue
            url = urlsplit(str(note.get('url') or note.get('permalink') or ''))
            permalink = urlunsplit((url.scheme, url.netloc, url.path, '', ''))
            work_id = note.get('platform_work_id') or note.get('note_id') or note.get('id')
            if not work_id and url.path:
                work_id = url.path.rstrip('/').split('/')[-1]
            if not work_id:
                continue
            metrics = note.get('metrics') or {key: note.get(key) for key in ('views', 'likes', 'comments', 'shares', 'collects')}
            works.append(WorkSnapshot(str(work_id), permalink, str(note.get('title') or ''), _metric_pairs(metrics)))
    seed = f'{owner}\0{name}\0{account_uid}\0{stamp}\0{source}\0{raw_hash}'
    return AnalyticsSnapshot(owner, name, stamp, payload.get('loggedIn', payload.get('logged_in')) is True,
                             tuple(sorted(account.items())), _metric_pairs(payload.get('content_metrics', payload.get('metrics'))),
                             source, hashlib.sha256(seed.encode()).hexdigest(), account_uid,
                             str(parsed.tzinfo), raw_hash, tuple(works))


def append_snapshot(root: Path, snapshot: AnalyticsSnapshot) -> bool:
    path = snapshot_path(root, snapshot.persona, snapshot.platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    if lock.is_symlink():
        raise SnapshotError('快照锁路径越界')
    with _storage_lock(lock):
        if any(row.snapshot_id == snapshot.snapshot_id for row in load_snapshots(root, snapshot.persona, snapshot.platform)):
            return False
        with path.open('a', encoding='utf-8') as output:
            output.write(json.dumps(snapshot.to_dict(), ensure_ascii=False, sort_keys=True) + '\n')
            output.flush()
            os.fsync(output.fileno())
    return True


def load_snapshots(root: Path, persona: str, platform: str) -> tuple[AnalyticsSnapshot, ...]:
    path = snapshot_path(root, persona, platform)
    if not path.is_file():
        return ()
    result: list[AnalyticsSnapshot] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                continue
            if item.get('persona') != persona or item.get('platform') != platform:
                continue
            works = tuple(WorkSnapshot(w['platform_work_id'], w['permalink'], w['title'],
                                       tuple(tuple(metric) for metric in w['metrics'])) for w in item.get('works', []))
            result.append(AnalyticsSnapshot(persona, platform, item['observed_at'], item.get('logged_in') is True,
                          _metric_pairs(item.get('account_metrics')), _metric_pairs(item.get('content_metrics')),
                          item['source'], item['snapshot_id'], item.get('account_uid'), item.get('timezone', 'UTC'),
                          item.get('raw_payload_hash', ''), works, item.get('schema_version', 1)))
        except (ValueError, KeyError, TypeError):
            continue
    return tuple(result)


def load_legacy_history(root: Path, platform: str) -> tuple[dict[str, JsonValue], ...]:
    """Read legacy rows without assigning their unknown ownership to a persona."""
    path = root / '_analytics' / f'{_safe_segment(platform, "平台")}.jsonl'
    if not path.is_file():
        return ()
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except ValueError:
            continue
    return tuple(rows)
