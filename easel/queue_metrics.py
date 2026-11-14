"""Durable operational observations recorded atomically with queue transitions.

Legacy rows supply current-state gauges only: historical timings cannot be inferred.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True, slots=True)
class MetricQueue:
    table: str
    name: str
    state: str = 'state'
    identifier: str = 'id'
    active: tuple[str, ...] = ('running',)
    success: tuple[str, ...] = ('succeeded',)
    failure: tuple[str, ...] = ('failed',)
    waiting: tuple[str, ...] = ('queued', 'waiting')
    single_attempt: bool = False


PUBLISH_TASK_QUEUE = MetricQueue('tasks', 'publish_tasks', active=('dispatching',), success=('published', 'succeeded'))
IMAGE_QUEUE = MetricQueue('generation_jobs', 'image')
VIDEO_QUEUE = MetricQueue('video_jobs', 'video', active=('submitting',), single_attempt=True)
AIDEO_QUEUE = MetricQueue('aideo_jobs', 'aideo', active=('unknown', 'running'), single_attempt=True)
DIRECT_EDIT_QUEUE = MetricQueue('direct_edit_jobs', 'direct_edit', active=('unknown', 'running'), single_attempt=True)
CHARACTER_QUEUE = MetricQueue('character_jobs', 'character', single_attempt=True)
DRAFT_QUEUE = MetricQueue('draft_jobs', 'draft')
CHAT_QUEUE = MetricQueue('chat_jobs', 'chat')
AGENT_QUEUE = MetricQueue('agent_tasks', 'agent', state='status', success=('completed', 'requires_action'), failure=('error', 'aborted'))
PUBLISH_QUEUE = MetricQueue('publish_runs', 'publish', identifier='run_id', active=('running', 'reconciling'), success=('published',), waiting=('queued', 'approved', 'retry_wait'))


def _values(items: tuple[str, ...]) -> str:
    return ','.join("'" + item + "'" for item in items)


def install_metrics(db: sqlite3.Connection, queue: MetricQueue) -> None:
    """Install only against trusted schema constants, before the store accepts jobs."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",
                  (f'metrics_{queue.table}_insert',)).fetchone() is not None:
        return
    db.execute('''CREATE TABLE IF NOT EXISTS operational_jobs(
        queue TEXT NOT NULL, job TEXT NOT NULL, state TEXT NOT NULL,
        entered REAL, started REAL, attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(queue,job))''')
    db.execute('''CREATE TABLE IF NOT EXISTS operational_observations(
        queue TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL)''')
    name, table, state, identifier = queue.name, queue.table, queue.state, queue.identifier
    db.execute(f'''INSERT OR IGNORE INTO operational_jobs(queue,job,state)
        SELECT '{name}',{identifier},{state} FROM {table}''')
    stamp = "((julianday('now')-2440587.5)*86400.0)"
    active, waiting = _values(queue.active), _values(queue.waiting)
    admission = f"NEW.{state} IN ({active})"
    fresh = "attempts=0" if queue.single_attempt else "started IS NULL"
    transitions = f'''
        INSERT INTO operational_observations SELECT '{name}','started',1 FROM operational_jobs WHERE queue='{name}' AND job=NEW.{identifier} AND {fresh} AND {admission};
        INSERT INTO operational_observations SELECT '{name}','attempts',1 FROM operational_jobs WHERE queue='{name}' AND job=NEW.{identifier} AND {fresh} AND {admission};
        INSERT INTO operational_observations SELECT '{name}','completed',1 WHERE NEW.{state} IN ({_values(queue.success)});
        INSERT INTO operational_observations SELECT '{name}','failed',1 WHERE NEW.{state} IN ({_values(queue.failure)});
    '''
    db.execute(f'''CREATE TRIGGER IF NOT EXISTS metrics_{table}_insert AFTER INSERT ON {table} BEGIN
        INSERT INTO operational_jobs(queue,job,state,entered) VALUES('{name}',NEW.{identifier},NEW.{state},{stamp});
        {transitions}
        UPDATE operational_jobs SET started={stamp},attempts=attempts+1 WHERE queue='{name}' AND job=NEW.{identifier}
            AND {fresh} AND {admission};
        END''')
    db.execute(f'''CREATE TRIGGER IF NOT EXISTS metrics_{table}_update AFTER UPDATE OF {state} ON {table}
        WHEN OLD.{state}<>NEW.{state} BEGIN
        INSERT INTO operational_observations SELECT '{name}','processing_seconds',max(0,{stamp}-started)
            FROM operational_jobs WHERE queue='{name}' AND job=NEW.{identifier}
            AND started IS NOT NULL AND NEW.{state} NOT IN ({active},'waiting');
        INSERT INTO operational_observations SELECT '{name}','waiting_seconds',max(0,{stamp}-entered)
            FROM operational_jobs WHERE queue='{name}' AND job=NEW.{identifier}
            AND entered IS NOT NULL AND OLD.{state} IN ({waiting}) AND {fresh} AND {admission};
        {transitions}
        UPDATE operational_jobs SET started=NULL WHERE queue='{name}' AND job=NEW.{identifier}
            AND NEW.{state} NOT IN ({active},'waiting');
        UPDATE operational_jobs SET started={stamp},attempts=attempts+1 WHERE queue='{name}' AND job=NEW.{identifier}
            AND {fresh} AND {admission};
        UPDATE operational_jobs SET state=NEW.{state},entered={stamp} WHERE queue='{name}' AND job=NEW.{identifier};
        END''')
    db.execute(f'''CREATE TRIGGER IF NOT EXISTS metrics_{table}_delete AFTER DELETE ON {table} BEGIN
        DELETE FROM operational_jobs WHERE queue='{name}' AND job=OLD.{identifier}; END''')


def render_queues(db: sqlite3.Connection) -> list[str]:
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='operational_jobs'").fetchone()
    if exists is None:
        return []
    lines = []
    for row in db.execute('SELECT queue,state,count(*) FROM operational_jobs GROUP BY queue,state'):
        lines.append(f'easel_queue_jobs{{queue="{row[0]}",state="{row[1]}"}} {row[2]}')
    for row in db.execute('SELECT queue,metric,sum(value),count(*) FROM operational_observations GROUP BY queue,metric'):
        queue, metric, total, count = row
        if metric.endswith('_seconds'):
            labels = f'queue="{queue}"'
            lines.extend((f'easel_queue_{metric}_sum{{{labels}}} {total}',
                          f'easel_queue_{metric}_count{{{labels}}} {count}'))
            for bound in (0.01, 0.1, 1, 5, 30, 120, 900):
                bucket = db.execute('SELECT count(*) FROM operational_observations WHERE queue=? AND metric=? AND value<=?',
                                    (queue, metric, bound)).fetchone()[0]
                lines.append(f'easel_queue_{metric}_bucket{{{labels},le="{bound}"}} {bucket}')
            lines.append(f'easel_queue_{metric}_bucket{{{labels},le="+Inf"}} {count}')
        else:
            lines.append(f'easel_queue_{metric}_total{{queue="{queue}"}} {total}')
    return lines


def render_draft_pools(db: sqlite3.Connection, threshold: int) -> list[str]:
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='draft_jobs'").fetchone() is None:
        return []
    rows = db.execute('''SELECT state,
        CASE WHEN state='queued' THEN CASE WHEN priority>=? THEN 'low' ELSE 'normal' END ELSE queue_class END AS pool,
        count(*) FROM draft_jobs GROUP BY state,pool''', (threshold,))
    return [f'easel_queue_pool_jobs{{queue="draft",pool="{row[1]}",state="{row[0]}"}} {row[2]}' for row in rows]
