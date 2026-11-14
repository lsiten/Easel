"""SQLite admission limits shared by every process and HTTP execution path."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Literal
from typing_extensions import assert_never

QueueKind = Literal['image', 'video', 'draft']


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    kind: QueueKind
    outputs: Path

    def limits(self) -> tuple[int, int, int]:
        from easel.generation_app_config import effective_config
        config = effective_config(self.outputs).queue
        match self.kind:
            case 'image':
                return config.imageConcurrency, 0, 2147483647
            case 'video':
                return config.videoConcurrency, 0, 2147483647
            case 'draft':
                return config.draftConcurrency, config.lowPriorityConcurrency, config.lowPriorityMinPriority
            case unreachable:
                assert_never(unreachable)

    def priority(self, model: str) -> int:
        from easel.generation_app_config import effective_config
        models = effective_config(self.outputs).models
        candidates = models.video.generation if self.kind == 'video' else (*models.image.generation, *models.image.edit)
        return next((item.queuePriority or 0 for item in candidates if item.name == model), 0)

    @property
    def table(self) -> str:
        return {'image': 'generation_jobs', 'video': 'video_jobs', 'draft': 'draft_jobs'}[self.kind]


def install_queue(db: sqlite3.Connection, policy: QueuePolicy) -> None:
    columns = {row['name'] for row in db.execute(f'PRAGMA table_info({policy.table})')}
    for name, declaration in (('priority', 'INTEGER NOT NULL DEFAULT 0'),
                              ('queue_class', "TEXT NOT NULL DEFAULT 'normal'")):
        if name not in columns:
            db.execute(f'ALTER TABLE {policy.table} ADD COLUMN {name} {declaration}')
    db.execute(f'CREATE INDEX IF NOT EXISTS idx_{policy.table}_priority ON {policy.table}(state,priority,created_at)')


def next_job(db: sqlite3.Connection, policy: QueuePolicy, identifier: str) -> sqlite3.Row | None:
    """Caller holds BEGIN IMMEDIATE through the subsequent owner/state update."""
    normal, low, threshold = policy.limits()
    table = policy.table
    active = 'submitting' if policy.kind == 'video' else 'running'
    available = "state IN ('queued','waiting') AND (ready_at<=unixepoch('now') OR id=?)" if policy.kind == 'video' else "state='queued'"
    parameters: list[str | int] = [identifier] if policy.kind == 'video' else []
    parameters.extend((identifier, identifier, threshold, low, normal))
    row = db.execute(f'''SELECT * FROM {table} AS candidate WHERE {available}
        AND (?='' OR id=?) AND
        (SELECT count(*) FROM {table} AS busy WHERE busy.state='{active}'
            AND busy.queue_class=CASE WHEN candidate.priority>=? THEN 'low' ELSE 'normal' END)
        < CASE WHEN candidate.priority>={threshold} THEN ? ELSE ? END
        ORDER BY priority,created_at,rowid LIMIT 1''', parameters).fetchone()
    if row is not None:
        db.execute(f'UPDATE {table} SET queue_class=? WHERE id=?',
                   ('low' if row['priority'] >= threshold else 'normal', row['id']))
    return row
