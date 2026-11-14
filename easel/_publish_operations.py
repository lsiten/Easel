"""Worker leases, durable effects, and publishing database operations."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import os
import tempfile
import uuid
from typing import Mapping

from easel.publish_models import (JsonValue, PublishRun, PublishEvent, PublishLease, PublishEffect,
    RunState, LedgerError, IdempotencyConflict, InvalidTransition, LeaseBusy, LeaseRequired,
    digest_mapping, _json, _now, _ALLOWED)
from easel._publish_database import PublishDatabase


class PublishOperations(PublishDatabase):
    def cancel_unsent(self, run_id: str, *, persona: str) -> PublishRun:
        """Fence local preparation when the owner cancels before external execution."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM publish_runs WHERE run_id=? AND persona=?', (run_id, persona)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row['state'] not in {'prepared', 'approved', 'queued'}:
                raise InvalidTransition('任务已进入外部执行阶段')
            now = _now()
            db.execute("UPDATE publish_runs SET state='cancelled',generation=generation+1,updated_at=? WHERE run_id=?", (now, run_id))
            db.execute('UPDATE publish_leases SET expires_at=0,fencing_token=fencing_token+1 WHERE run_id=?', (run_id,))
            db.execute('''INSERT INTO publish_events(event_id,run_id,sequence,event_type,from_state,to_state,actor,payload_json,created_at)
                VALUES(?,?,(SELECT COALESCE(MAX(sequence),0)+1 FROM publish_events WHERE run_id=?),
                'cancellation',?,'cancelled','task-control','{}',?)''', (uuid.uuid4().hex, run_id, run_id, row['state'], now))
            return self._row_to_run(db.execute('SELECT * FROM publish_runs WHERE run_id=?', (run_id,)).fetchone())

    def checkpoint_run(self, run_id: str, *, expected: RunState,
                       payload: Mapping[str, JsonValue], fencing_token: int,
                       external_request_id: str = '', platform_work_id: str = '',
                       permalink: str = '', target: RunState | None = None,
                       requeue_projection: bool = False,
                       actor: str = 'channel-publisher') -> PublishRun:
        """Persist trusted provider progress under a current worker lease."""
        destination = target or expected
        if destination != expected and destination not in _ALLOWED.get(expected, frozenset()):
            raise InvalidTransition(f'{expected.value} -> {destination.value} 不允许')
        now = _now()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM publish_runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row['state'] != expected.value:
                raise InvalidTransition('发布状态已改变')
            lease = db.execute('SELECT * FROM publish_leases WHERE run_id=?', (run_id,)).fetchone()
            if lease is None or lease['expires_at'] <= datetime.now(timezone.utc).timestamp() or lease['fencing_token'] != fencing_token:
                raise LeaseRequired('缺少当前有效 worker 租约')
            merged = {**json.loads(row['payload_json']), **payload}
            db.execute('''UPDATE publish_runs SET payload_json=?,state=?,generation=generation+1,
                updated_at=?,external_request_id=COALESCE(NULLIF(?,''),external_request_id),
                platform_work_id=COALESCE(NULLIF(?,''),platform_work_id),
                permalink=COALESCE(NULLIF(?,''),permalink) WHERE run_id=?''',
                (_json(merged), destination.value, now, external_request_id, platform_work_id, permalink, run_id))
            db.execute('''INSERT INTO publish_events
                (event_id,run_id,sequence,event_type,from_state,to_state,actor,payload_json,created_at)
                VALUES(?,?,(SELECT COALESCE(MAX(sequence),0)+1 FROM publish_events WHERE run_id=?),
                'checkpoint',?,?,?,?,?)''',
                (uuid.uuid4().hex, run_id, run_id, expected.value, destination.value, actor,
                 _json({'fields': sorted(payload), 'has_external_request': bool(external_request_id),
                        'platform_work_id': platform_work_id}), now))
            updated = db.execute('SELECT * FROM publish_runs WHERE run_id=?', (run_id,)).fetchone()
            assert updated is not None
            if destination == RunState.PUBLISHED:
                self._enqueue_publication_effect(db, updated, now, refresh=requeue_projection)
            return self._row_to_run(updated)

    @staticmethod
    def _enqueue_publication_effect(db: sqlite3.Connection, row: sqlite3.Row, now: str, *, refresh: bool = False) -> None:
        payload = json.loads(row['payload_json'])
        previous = db.execute("SELECT payload_json FROM publish_effects WHERE run_id=? AND effect_type='publish_projection'", (row['run_id'],)).fetchone()
        original = json.loads(previous['payload_json']) if previous else {}
        effect = {'platform_name': payload.get('platform_name', row['platform']),
                  'title': payload.get('title', ''), 'tags': payload.get('tags', ''),
                  'publish_type': payload.get('publish_type', ''),
                  'permalink': row['permalink'], 'published_at': payload.get('published_at') or original.get('published_at') or now}
        db.execute('''INSERT OR IGNORE INTO publish_effects(effect_key,run_id,effect_type,
            status,payload_json,created_at) VALUES(?,?,'publish_projection','pending',?,?)''',
            (f"published:{row['run_id']}", row['run_id'], _json(effect), now))
        if refresh:
            db.execute("UPDATE publish_effects SET status='pending',payload_json=?,completed_at=NULL WHERE run_id=? AND effect_type='publish_projection'",
                       (_json(effect), row['run_id']))

    def acquire_lease(self, run_id: str, owner: str, ttl_seconds: float = 120.0) -> PublishLease:
        if ttl_seconds <= 0 or not owner.strip():
            raise ValueError("owner 和 ttl_seconds 必须有效")
        now = datetime.now(timezone.utc).timestamp()
        expires = now + ttl_seconds
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM publish_runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise KeyError(run_id)
            competing = db.execute('''SELECT l.run_id FROM publish_leases l
                JOIN publish_runs active ON active.run_id=l.run_id
                JOIN publish_runs requested ON requested.run_id=?
                WHERE active.persona=requested.persona AND active.platform=requested.platform
                  AND active.account_id=requested.account_id AND active.run_id!=requested.run_id
                  AND active.state='running' AND l.expires_at>? LIMIT 1''', (run_id, now)).fetchone()
            if competing is not None:
                raise LeaseBusy('该账号已有进行中的发布')
            row = db.execute("SELECT * FROM publish_leases WHERE run_id=?", (run_id,)).fetchone()
            if row is not None and row["expires_at"] > now and row["owner"] != owner:
                raise LeaseBusy(run_id)
            token = int(row["fencing_token"]) + 1 if row is not None else 1
            db.execute(
                """INSERT INTO publish_leases(run_id, owner, fencing_token, expires_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET owner=excluded.owner,
                fencing_token=excluded.fencing_token, expires_at=excluded.expires_at""",
                (run_id, owner, token, expires),
            )
            return PublishLease(run_id, owner, token, expires)

    def release_lease(self, run_id: str, owner: str, fencing_token: int) -> bool:
        """Expire a lease while retaining its token to prevent ABA reuse."""
        with self._connect() as db:
            result = db.execute(
                "UPDATE publish_leases SET expires_at=0 WHERE run_id=? AND owner=? AND fencing_token=?",
                (run_id, owner, fencing_token),
            )
            return result.rowcount == 1

    def renew_lease(self, run_id: str, owner: str, fencing_token: int,
                    ttl_seconds: float = 120) -> PublishLease:
        if ttl_seconds <= 0:
            raise ValueError('ttl_seconds 必须为正数')
        now = datetime.now(timezone.utc).timestamp()
        expires = now + ttl_seconds
        with self._connect() as db:
            updated = db.execute('''UPDATE publish_leases SET expires_at=?
                WHERE run_id=? AND owner=? AND fencing_token=? AND expires_at>?''',
                (expires, run_id, owner, fencing_token, now))
            if updated.rowcount != 1:
                raise LeaseRequired('worker 租约已失效')
        return PublishLease(run_id, owner, fencing_token, expires)

    def record_effect(
        self,
        run_id: str,
        *,
        effect_key: str,
        effect_type: str,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> PublishEffect:
        if not effect_key.strip() or not effect_type.strip():
            raise ValueError("effect_key 和 effect_type 不能为空")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM publish_runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise KeyError(run_id)
            existing = db.execute('SELECT * FROM publish_effects WHERE run_id=? AND effect_type=?',
                                  (run_id, effect_type)).fetchone()
            if existing is not None:
                if existing['effect_key'] != effect_key:
                    raise IdempotencyConflict('该 run 的副作用已绑定其他 key')
                return self._row_to_effect(existing)
            db.execute(
                "INSERT OR IGNORE INTO publish_effects(effect_key, run_id, effect_type, status, payload_json, created_at) VALUES(?, ?, ?, 'pending', ?, ?)",
                (effect_key, run_id, effect_type, _json(payload or {}), now),
            )
            row = db.execute("SELECT * FROM publish_effects WHERE effect_key=?", (effect_key,)).fetchone()
            assert row is not None
            if row["run_id"] != run_id or row["effect_type"] != effect_type:
                raise IdempotencyConflict(f"effect_key 已绑定 {row['run_id']}")
            return self._row_to_effect(row)

    def complete_effect(self, effect_key: str, *, status: str, payload: Mapping[str, JsonValue] | None = None) -> PublishEffect:
        if not status.strip():
            raise ValueError("status 不能为空")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM publish_effects WHERE effect_key=?", (effect_key,)).fetchone()
            if row is None:
                raise KeyError(effect_key)
            merged = dict(json.loads(row["payload_json"]))
            merged.update(payload or {})
            completed = _now()
            db.execute("UPDATE publish_effects SET status=?, payload_json=?, completed_at=? WHERE effect_key=?", (status, _json(merged), completed, effect_key))
            updated = db.execute("SELECT * FROM publish_effects WHERE effect_key=?", (effect_key,)).fetchone()
            assert updated is not None
            return self._row_to_effect(updated)

    def pending_effects(self, effect_type: str | None = None) -> tuple[PublishEffect, ...]:
        with self._connect() as db:
            if effect_type is None:
                rows = db.execute("SELECT * FROM publish_effects WHERE status!='succeeded' ORDER BY created_at").fetchall()
            else:
                rows = db.execute("SELECT * FROM publish_effects WHERE status!='succeeded' AND effect_type=? ORDER BY created_at",
                                  (effect_type,)).fetchall()
            return tuple(self._row_to_effect(row) for row in rows)

    def integrity_check(self) -> tuple[str, ...]:
        with self._connect() as db:
            rows = db.execute("PRAGMA integrity_check").fetchall()
            return tuple(str(row[0]) for row in rows)

    def checkpoint(self) -> None:
        with self._connect() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def backup(self, destination: Path) -> None:
        if destination.is_symlink():
            raise ValueError('备份目标不能是符号链接')
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(dir=destination.parent, prefix='.publish-backup-')
        os.close(descriptor)
        temporary = Path(name)
        target = sqlite3.connect(temporary)
        try:
            with self._connect() as source, target:
                source.backup(target)
            target.close()
            with temporary.open('rb') as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            target.close()
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _row_to_effect(row: sqlite3.Row) -> PublishEffect:
        return PublishEffect(
            effect_key=row["effect_key"], run_id=row["run_id"], effect_type=row["effect_type"],
            status=row["status"], payload=json.loads(row["payload_json"]),
            created_at=row["created_at"], completed_at=row["completed_at"],
        )
