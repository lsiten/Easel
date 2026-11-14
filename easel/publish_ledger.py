"""Durable persona-scoped publishing intents and state transitions."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import uuid
from typing import Mapping

from easel.publish_models import (JsonValue, PublishRun, PublishEvent, PublishLease, PublishEffect,
    RunState, LedgerError, IdempotencyConflict, InvalidTransition, LeaseBusy, LeaseRequired,
    digest_mapping, content_identity, _json, _now, _ALLOWED)
from easel._publish_operations import PublishOperations


class PublishLedger(PublishOperations):
    def create_run(
        self,
        *,
        platform: str,
        persona: str,
        idempotency_key: str,
        approval_digest: str,
        media_manifest_hash: str,
        account_id: str = "",
        payload: Mapping[str, JsonValue] | None = None,
        initial_state: RunState = RunState.PREPARED,
    ) -> PublishRun:
        if not platform.strip() or not persona.strip() or not idempotency_key.strip():
            raise ValueError("platform、persona 和 idempotency_key 不能为空")
        if initial_state not in (RunState.PREPARED, RunState.READY_FOR_MANUAL_SUBMISSION):
            raise ValueError("非法初始状态")
        body = payload or {}
        now = _now()
        run_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            referenced = db.execute('SELECT * FROM publish_runs WHERE run_id=?', (idempotency_key,)).fetchone()
            if referenced is not None and (referenced['persona'], referenced['platform']) != (persona, platform):
                raise IdempotencyConflict('run_id 不属于当前画像或平台')
            existing = db.execute(
                "SELECT * FROM publish_runs WHERE persona=? AND platform=? AND (idempotency_key=? OR run_id=?)",
                (persona, platform, idempotency_key, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (existing["approval_digest"], existing["media_manifest_hash"], existing["account_id"]) != (
                    approval_digest, media_manifest_hash, account_id
                ):
                    raise IdempotencyConflict(f"idempotency_key 已绑定 run {existing['run_id']}")
                return self._row_to_run(existing)
            uncertain = db.execute('''SELECT payload_json FROM publish_runs
                WHERE persona=? AND platform=? AND account_id=? AND media_manifest_hash=?
                AND state IN ('unknown','submitted')''',
                (persona, platform, account_id, media_manifest_hash)).fetchall()
            if any(content_identity(json.loads(row['payload_json'])) == content_identity(body) for row in uncertain):
                raise IdempotencyConflict('相同内容仍有待核验的平台结果，请先完成作品回读')
            db.execute(
                """INSERT INTO publish_runs
                (run_id, platform, persona, idempotency_key, state, approval_digest,
                 media_manifest_hash, account_id, payload_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, platform, persona, idempotency_key, initial_state.value,
                 approval_digest, media_manifest_hash, account_id, _json(body), now, now),
            )
            db.execute(
                """INSERT INTO publish_events
                (event_id, run_id, sequence, event_type, from_state, to_state, actor, payload_json, created_at)
                VALUES(?, ?, 1, 'created', '', ?, 'system', ?, ?)""",
                (uuid.uuid4().hex, run_id, initial_state.value, _json(body), now),
            )
            row = db.execute("SELECT * FROM publish_runs WHERE run_id=?", (run_id,)).fetchone()
            assert row is not None
            return self._row_to_run(row)

    def get_run(self, run_id: str) -> PublishRun | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM publish_runs WHERE run_id=?", (run_id,)).fetchone()
            return self._row_to_run(row) if row is not None else None

    def latest_run(self, *, persona: str, platform: str) -> PublishRun | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM publish_runs WHERE persona=? AND platform=? ORDER BY created_at DESC LIMIT 1",
                (persona, platform),
            ).fetchone()
            return self._row_to_run(row) if row is not None else None

    def list_runs(self, *, persona: str, platform: str = '') -> tuple[PublishRun, ...]:
        with self._connect() as db:
            rows = db.execute('SELECT * FROM publish_runs WHERE persona=? AND (? = ? OR platform=?) ORDER BY created_at DESC',
                              (persona, platform, '', platform)).fetchall()
            return tuple(self._row_to_run(row) for row in rows)

    def reconcile(
        self,
        run_id: str,
        *,
        account_id: str,
        outcome: str,
        platform_work_id: str = "",
        permalink: str = "",
        published_at: str = "",
        actor: str = "reconciler",
    ) -> PublishRun:
        """Record a platform read-back without guessing on ambiguous results."""
        if outcome not in {"verified", "ambiguous", "unverified"}:
            raise ValueError("非法 reconciliation outcome")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM publish_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["account_id"] != account_id:
                raise LeaseRequired("账号作用域不匹配")
            lease = db.execute('SELECT * FROM publish_leases WHERE run_id=?', (run_id,)).fetchone()
            if lease is not None and lease['expires_at'] > datetime.now(timezone.utc).timestamp():
                raise LeaseRequired('发布 worker 仍持有租约')
            current = RunState(row["state"])
            if published_at:
                observed = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                if observed.tzinfo is None:
                    raise ValueError('published_at 必须包含时区')
                body = json.loads(row['payload_json'])
                body['published_at'] = observed.astimezone(timezone.utc).isoformat()
                db.execute('UPDATE publish_runs SET payload_json=? WHERE run_id=?', (_json(body), run_id))
            if outcome == "verified" and not (platform_work_id.strip() or permalink.strip()):
                raise ValueError("verified reconciliation 必须包含作品 ID 或 permalink")
            target = RunState.PUBLISHED if outcome == "verified" else RunState.UNKNOWN
            if target != current:
                if target not in _ALLOWED.get(current, frozenset()):
                    raise InvalidTransition(f"{current.value} -> {target.value} 不允许")
                now = _now()
                generation = int(row["generation"]) + 1
                db.execute(
                    "UPDATE publish_runs SET state=?, updated_at=?, generation=?, platform_work_id=?, permalink=? WHERE run_id=? AND state=? AND generation=?",
                    (target.value, now, generation, platform_work_id.strip(), permalink.strip(), run_id, current.value, row["generation"]),
                )
                payload = {"outcome": outcome, "platform_work_id": platform_work_id.strip(), "permalink": permalink.strip()}
                db.execute(
                    """INSERT INTO publish_events
                    (event_id, run_id, sequence, event_type, from_state, to_state, actor, payload_json, created_at)
                    VALUES(?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM publish_events WHERE run_id=?),
                           'reconciliation', ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex, run_id, run_id, current.value, target.value, actor, _json(payload), now),
                )
            else:
                db.execute(
                    "UPDATE publish_runs SET platform_work_id=COALESCE(NULLIF(?, ''), platform_work_id), permalink=COALESCE(NULLIF(?, ''), permalink) WHERE run_id=?",
                    (platform_work_id.strip(), permalink.strip(), run_id),
                )
            updated = db.execute("SELECT * FROM publish_runs WHERE run_id=?", (run_id,)).fetchone()
            assert updated is not None
            if target == RunState.PUBLISHED:
                self._enqueue_publication_effect(db, updated, _now())
            return self._row_to_run(updated)

    def list_events(self, run_id: str) -> tuple[PublishEvent, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM publish_events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
            return tuple(self._row_to_event(row) for row in rows)

    def transition(
        self,
        run_id: str,
        *,
        expected: RunState,
        target: RunState,
        actor: str,
        payload: Mapping[str, JsonValue] | None = None,
        fencing_token: int | None = None,
    ) -> PublishRun:
        if target not in _ALLOWED.get(expected, frozenset()):
            raise InvalidTransition(f"{expected.value} -> {target.value} 不允许")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM publish_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row["state"] != expected.value:
                raise InvalidTransition(f"run 当前为 {row['state']}，期望 {expected.value}")
            lease = db.execute("SELECT * FROM publish_leases WHERE run_id=?", (run_id,)).fetchone()
            if lease is not None:
                if lease["expires_at"] <= datetime.now(timezone.utc).timestamp():
                    raise LeaseRequired("run lease 已过期")
                if fencing_token != lease["fencing_token"]:
                    raise LeaseRequired("缺少当前 fencing token")
            generation = int(row["generation"]) + 1
            attempts = int(row["attempts"])
            if target in (RunState.RUNNING, RunState.RECONCILING):
                attempts += 1
            db.execute(
                "UPDATE publish_runs SET state=?, updated_at=?, generation=?, attempts=?, last_error=? WHERE run_id=? AND state=? AND generation=?",
                (target.value, now, generation, attempts,
                 str((payload or {}).get("error", "")) if target == RunState.FAILED else "",
                 run_id, expected.value, row["generation"]),
            )
            db.execute(
                """INSERT INTO publish_events
                (event_id, run_id, sequence, event_type, from_state, to_state, actor, payload_json, created_at)
                VALUES(?, ?, (SELECT COALESCE(MAX(sequence), 0) + 1 FROM publish_events WHERE run_id=?),
                       'transition', ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, run_id, run_id, expected.value, target.value, actor, _json(payload or {}), now),
            )
            updated = db.execute("SELECT * FROM publish_runs WHERE run_id=?", (run_id,)).fetchone()
            assert updated is not None
            if target == RunState.PUBLISHED:
                self._enqueue_publication_effect(db, updated, now)
            return self._row_to_run(updated)

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> PublishRun:
        return PublishRun(
            run_id=row["run_id"], platform=row["platform"], persona=row["persona"],
            idempotency_key=row["idempotency_key"], state=RunState(row["state"]),
            approval_digest=row["approval_digest"], media_manifest_hash=row["media_manifest_hash"],
            account_id=row["account_id"], created_at=row["created_at"], updated_at=row["updated_at"],
            generation=int(row["generation"]), attempts=int(row["attempts"]),
            last_error=row["last_error"],
            external_request_id=row["external_request_id"],
            platform_work_id=row["platform_work_id"], permalink=row["permalink"],
            payload=json.loads(row["payload_json"]),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> PublishEvent:
        return PublishEvent(
            event_id=row["event_id"], run_id=row["run_id"], sequence=int(row["sequence"]),
            event_type=row["event_type"], from_state=row["from_state"], to_state=row["to_state"],
            actor=row["actor"], payload=json.loads(row["payload_json"]), created_at=row["created_at"],
        )
