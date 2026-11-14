"""SQLite publishing schema and connection lifetime."""
from __future__ import annotations
from easel.queue_metrics import install_metrics, PUBLISH_QUEUE

from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import os
import json
import sqlite3
import uuid

from easel.publish_models import _now


class PublishDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        if path.is_symlink():
            raise ValueError('发布账本不能是符号链接')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if os.name != 'nt':
            self.path.chmod(0o600)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publish_runs (
                    run_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    persona TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    approval_digest TEXT NOT NULL,
                    media_manifest_hash TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    external_request_id TEXT NOT NULL DEFAULT '',
                    platform_work_id TEXT NOT NULL DEFAULT '',
                    permalink TEXT NOT NULL DEFAULT '',
                    UNIQUE(persona, platform, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_publish_runs_scope
                    ON publish_runs(persona, platform, created_at);
                CREATE TABLE IF NOT EXISTS publish_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES publish_runs(run_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS publish_leases (
                    run_id TEXT PRIMARY KEY REFERENCES publish_runs(run_id) ON DELETE CASCADE,
                    owner TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publish_effects (
                    effect_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES publish_runs(run_id) ON DELETE CASCADE,
                    effect_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_publish_effects_run ON publish_effects(run_id, status);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(publish_runs)").fetchall()}
            for name in ("external_request_id", "platform_work_id", "permalink"):
                if name not in columns:
                    db.execute(f"ALTER TABLE publish_runs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            db.execute('BEGIN IMMEDIATE')
            duplicates = db.execute('''SELECT run_id,effect_type FROM publish_effects
                GROUP BY run_id,effect_type HAVING COUNT(*)>1''').fetchall()
            for duplicate in duplicates:
                effects = db.execute('''SELECT effect_key FROM publish_effects
                    WHERE run_id=? AND effect_type=?
                    ORDER BY (status='succeeded') DESC, created_at, effect_key''', tuple(duplicate)).fetchall()
                removed = [row['effect_key'] for row in effects[1:]]
                db.executemany('DELETE FROM publish_effects WHERE effect_key=?', ((key,) for key in removed))
                db.execute('''INSERT INTO publish_events
                    (event_id,run_id,sequence,event_type,from_state,to_state,actor,payload_json,created_at)
                    VALUES(?,?,(SELECT COALESCE(MAX(sequence),0)+1 FROM publish_events WHERE run_id=?),
                    'effect_migration','','','schema',?,?)''',
                    (uuid.uuid4().hex, duplicate['run_id'], duplicate['run_id'],
                     json.dumps({'retained': effects[0]['effect_key'], 'superseded': removed}), _now()))
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_publish_effects_once ON publish_effects(run_id,effect_type)')
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(?, ?)",
                (1, _now()),
            )
            db.execute('INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(2,?)', (_now(),))
            install_metrics(db, PUBLISH_QUEUE)


    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            with connection:
                yield connection
        finally:
            connection.close()
