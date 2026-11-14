"""Replay-safe local projections of confirmed publication events."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from pydantic import JsonValue

from easel.persona import valid_persona_name
from easel.publish_ledger import PublishLedger
from easel.publish_models import PublishEffect, RunState
from easel.publish_context import PublishContext, apply_usage
from skills.shared.scripts.asset_storage import _storage_lock
from skills.shared.scripts.calendar_ops import CST


@dataclass(frozen=True, slots=True)
class PublishedProjection:
    platform_name: str
    title: str
    tags: str = ''
    publish_type: str = ''
    permalink: str = ''


@dataclass(frozen=True, slots=True)
class ProjectionDrain:
    applied: tuple[str, ...]
    pending: tuple[str, ...]


class ProjectionError(ValueError):
    """Projection cannot be safely applied to the current local store."""

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def queue_publish_projection(ledger: PublishLedger, run_id: str,
                             projection: PublishedProjection) -> PublishEffect:
    """Persist one run-level effect before touching either projection target."""
    run = ledger.get_run(run_id)
    if run is None or run.state != RunState.PUBLISHED:
        raise ProjectionError('Only a confirmed published run can enter projections')
    payload = asdict(projection)
    payload['published_at'] = run.payload.get('published_at') or run.updated_at
    return ledger.record_effect(run_id, effect_key=f'published:{run_id}',
                                effect_type='publish_projection', payload=payload)


def _paths(outputs: Path, persona: str) -> tuple[Path, Path]:
    if persona == '_general':
        calendar = outputs / '_schedule.json'
        publish_log = outputs / '_analytics' / 'publish-log.json'
    else:
        if not valid_persona_name(persona):
            raise ProjectionError('Invalid projection persona')
        calendar = outputs / '_schedule' / f'{persona}.json'
        publish_log = outputs / '_analytics' / 'publish-log' / f'{persona}.json'
    for target in (calendar, publish_log):
        if target.is_symlink() or not target.resolve().is_relative_to(outputs.resolve()):
            raise ProjectionError('Projection path escapes the output root')
        if target.with_suffix('.lock').is_symlink():
            raise ProjectionError('Projection lock cannot be a symlink')
    return calendar, publish_log


def _atomic_json(path: Path, data: JsonValue) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _calendar_row(effect: PublishEffect, persona: str) -> JsonValue:
    stamp = str(effect.payload['published_at'])
    observed = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(CST)
    return {'id': effect.run_id, 'run_id': effect.run_id, 'profile': persona,
            'platform': effect.payload['platform_name'], 'title': effect.payload['title'],
            'date': observed.date().isoformat(), 'time': observed.strftime('%H:%M'),
            'kind': 'content', 'status': 'published', 'note': '',
            'source': 'publish-page', 'url': effect.payload.get('permalink', '')}


def _upsert_calendar(path: Path, effect: PublishEffect, persona: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _storage_lock(path.with_suffix('.lock')):
        rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if not isinstance(rows, list):
            raise ProjectionError('Calendar store is malformed')
        if any(not isinstance(row, dict) for row in rows):
            raise ProjectionError('Calendar row is malformed')
        old = next((row for row in rows if row.get('run_id') == effect.run_id), {})
        retained = [row for row in rows if row.get('run_id') != effect.run_id]
        item = {**old, **_calendar_row(effect, persona), 'note': old.get('note', '')}
        _atomic_json(path, [*retained, item])


def _upsert_publish_log(path: Path, effect: PublishEffect, persona: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _storage_lock(path.with_suffix('.lock')):
        store = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'version': '1.0', 'entries': []}
        if not isinstance(store, dict) or not isinstance(store.get('entries'), list):
            raise ProjectionError('Publish log store is malformed')
        entries = store['entries']
        if any(not isinstance(entry, dict) for entry in entries):
            raise ProjectionError('Publish log row is malformed')
        old = next((entry for entry in entries if entry.get('run_id') == effect.run_id), None)
        entry_id = old['id'] if old else max((entry.get('id', 0) for entry in entries), default=0) + 1
        tags = str(effect.payload.get('tags', ''))
        item = {'id': entry_id, 'run_id': effect.run_id, 'profile': persona,
                'platform': effect.payload['platform_name'], 'title': effect.payload['title'],
                'url': effect.payload.get('permalink', ''), 'type': effect.payload.get('publish_type', ''),
                'published_at': effect.payload['published_at'], 'logged_at': effect.created_at,
                'initial_metrics': {key: None for key in ('views', 'likes', 'comments', 'shares')},
                'skill_source': 'publish-page', 'tags': [tag.strip() for tag in tags.split(',') if tag.strip()],
                'notes': '', 'persona_check': {'score': None, 'verdict': ''}}
        if old:
            item = {**old, **item,
                    **{key: old[key] for key in ('initial_metrics', 'notes', 'persona_check', 'tags') if key in old}}
        store['entries'] = [entry for entry in entries if entry.get('run_id') != effect.run_id] + [item]
        _atomic_json(path, store)


def deliver_publish_projection(ledger: PublishLedger, outputs: Path, effect: PublishEffect) -> None:
    """Apply both idempotent targets, then acknowledge their durable effect."""
    if effect.status == 'succeeded':
        return
    if effect.effect_type != 'publish_projection':
        raise ProjectionError('Unexpected projection effect type')
    run = ledger.get_run(effect.run_id)
    if run is None or run.state != RunState.PUBLISHED:
        raise ProjectionError('Projection run is not published')
    calendar, publish_log = _paths(outputs, run.persona)
    _upsert_calendar(calendar, effect, run.persona)
    _upsert_publish_log(publish_log, effect, run.persona)
    context = PublishContext.model_validate(run.payload.get('context') or {})
    if context.material_id or context.media_ids:
        apply_usage(outputs, run.persona, run.run_id, context)
    ledger.complete_effect(effect.effect_key, status='succeeded')


def drain_publish_projections(ledger: PublishLedger, outputs: Path) -> ProjectionDrain:
    """Retry incomplete effects after restart without duplicating their targets."""
    applied: list[str] = []
    pending: list[str] = []
    for effect in ledger.pending_effects(effect_type='publish_projection'):
        try:
            deliver_publish_projection(ledger, outputs, effect)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            pending.append(effect.effect_key)
        else:
            applied.append(effect.effect_key)
    return ProjectionDrain(tuple(applied), tuple(pending))
