"""Durable multi-target publication flows and due-task claiming."""
from __future__ import annotations
from easel.queue_metrics import install_metrics, PUBLISH_TASK_QUEUE

from contextlib import contextmanager
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from pydantic import BaseModel, Field, JsonValue, StrictBool

from easel.publish_models import digest_mapping


class FlowItem(BaseModel):
    platform: str
    account_uid: str = ''
    overrides: dict[str, JsonValue] = Field(default_factory=dict)
    approval_digest: str
    approval_expires_at: float | None = None


class FlowRequest(BaseModel):
    persona: str
    flow_id: str = Field(min_length=1, max_length=200)
    publish_at: datetime
    content: dict[str, JsonValue]
    items: tuple[FlowItem, ...] = Field(min_length=1, max_length=30)
    reviewed: StrictBool
    context: dict[str, JsonValue] = Field(default_factory=dict)


class TaskStore:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'tasks.sqlite3'
        if self.path.is_symlink():
            raise ValueError('任务存储不能是符号链接')
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS flows(
                id TEXT PRIMARY KEY,persona TEXT NOT NULL,caller_id TEXT NOT NULL,
                digest TEXT NOT NULL,context_json TEXT NOT NULL,created_at REAL NOT NULL,
                UNIQUE(persona,caller_id));
                CREATE TABLE IF NOT EXISTS tasks(
                id TEXT PRIMARY KEY,flow_id TEXT NOT NULL REFERENCES flows(id),
                persona TEXT NOT NULL,platform TEXT NOT NULL,account_uid TEXT NOT NULL,
                publish_at REAL NOT NULL,state TEXT NOT NULL,payload_json TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',owner TEXT NOT NULL DEFAULT '',
                result_json TEXT NOT NULL DEFAULT '{}',attempts INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS tasks_due ON tasks(state,publish_at);''')
            if 'next_attempt' not in {row['name'] for row in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN next_attempt REAL NOT NULL DEFAULT 0')
            install_metrics(db, PUBLISH_TASK_QUEUE)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request: FlowRequest, prepare: Callable[[str, dict], dict] | None = None) -> dict:
        if not request.reviewed or request.publish_at.tzinfo is None:
            raise ValueError('发布计划需要人工审核并指定带时区的时间')
        stamp = request.publish_at.timestamp()
        now = datetime.now(timezone.utc).timestamp()
        if stamp > now + 30 * 86400:
            raise ValueError('发布时间最多提前30天')
        digest = digest_mapping(request.model_dump(mode='json'))
        new_tasks: list[str] = []
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM flows WHERE persona=? AND caller_id=?',
                             (request.persona, request.flow_id)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise ValueError('flow_id 已绑定其他审核内容')
                return self._flow(db, row)
            flow_id = uuid.uuid4().hex
            db.execute('INSERT INTO flows VALUES(?,?,?,?,?,?)',
                       (flow_id, request.persona, request.flow_id, digest, json.dumps(request.context), now))
            for item in request.items:
                task_id = uuid.uuid4().hex
                payload = {**request.content, **item.overrides, 'persona': request.persona,
                           'account_uid': item.account_uid, 'reviewed': True,
                           'approval_digest': item.approval_digest, 'idempotency_key': task_id,
                           'context': request.context,
                           'run_id': task_id, 'scheduled_for': request.publish_at.isoformat()}
                if item.approval_expires_at is not None:
                    payload['approval_expires_at'] = item.approval_expires_at
                db.execute('''INSERT INTO tasks(id,flow_id,persona,platform,account_uid,publish_at,state,payload_json)
                    VALUES(?,?,?,?,?,?,?,?)''',
                    (task_id, flow_id, request.persona, item.platform, item.account_uid, stamp,
                     'preparing' if prepare else 'queued', json.dumps(payload)))
                new_tasks.append(task_id)
        if prepare is not None:
            from easel.publish_task_preparation import prepare_one
            for task_id in new_tasks:
                prepare_one(self, prepare, task_id)
        return self.flow(request.persona, flow_id)

    @staticmethod
    def _task(row: sqlite3.Row) -> dict:
        payload = json.loads(row['payload_json'])
        intent = {key: payload[key] for key in ('persona', 'account_uid', 'title', 'body', 'media', 'options', 'context', 'scheduled_for', 'cover_path', 'tags') if key in payload}
        return {key: row[key] for key in ('id', 'flow_id', 'persona', 'platform', 'account_uid', 'publish_at', 'state', 'run_id', 'attempts')} | {'result': json.loads(row['result_json']), 'intent': intent}

    def _flow(self, db: sqlite3.Connection, row: sqlite3.Row) -> dict:
        return {'id': row['id'], 'flow_id': row['caller_id'], 'persona': row['persona'],
                'context': json.loads(row['context_json']),
                'tasks': [self._task(task) for task in db.execute('SELECT * FROM tasks WHERE flow_id=? ORDER BY rowid', (row['id'],))]}

    def flow(self, persona: str, flow_id: str) -> dict:
        with self.connect() as db:
            row = db.execute('SELECT * FROM flows WHERE id=? AND persona=?', (flow_id, persona)).fetchone()
            if row is None:
                raise KeyError(flow_id)
            return self._flow(db, row)

    def has_flow(self, persona: str, caller_id: str) -> bool:
        with self.connect() as db:
            return db.execute('SELECT 1 FROM flows WHERE persona=? AND caller_id=?', (persona, caller_id)).fetchone() is not None

    def flows(self, persona: str, limit: int = 50, offset: int = 0) -> dict:
        with self.connect() as db:
            rows = db.execute('SELECT * FROM flows WHERE persona=? ORDER BY created_at DESC LIMIT ? OFFSET ?', (persona, limit, offset)).fetchall()
            total = db.execute('SELECT COUNT(*) FROM flows WHERE persona=?', (persona,)).fetchone()[0]
            items = [self._flow(db, row) for row in rows]
        for flow in items:
            for task in flow['tasks']:
                task.pop('intent', None)
        return {'total': total, 'items': items}

    def task(self, persona: str, task_id: str) -> dict:
        with self.connect() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=? AND persona=?', (task_id, persona)).fetchone()
            if row is None:
                raise KeyError(task_id)
            return self._task(row)

    def claim(self, owner: str, now: float) -> tuple[str, str, dict] | None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM tasks WHERE state='queued' AND publish_at<=? AND next_attempt<=? ORDER BY publish_at,rowid LIMIT 1", (now, now)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE tasks SET state='dispatching',owner=?,attempts=attempts+1 WHERE id=?", (owner, row['id']))
            return row['id'], row['platform'], json.loads(row['payload_json'])

    def finish(self, task_id: str, owner: str, state: str, result: dict) -> bool:
        public = {key: result[key] for key in ('run_id', 'state', 'message', 'error', 'platform_work_id', 'permalink',
                  'visibility', 'work_link_status', 'cover_status', 'cover_error') if key in result}
        with self.connect() as db:
            retry_at = datetime.now(timezone.utc).timestamp() + 15 if state == 'queued' else 0
            changed = db.execute("UPDATE tasks SET state=?,result_json=?,run_id=?,next_attempt=? WHERE id=? AND owner=? AND state='dispatching'",
                                 (state, json.dumps(public), result.get('run_id', ''), retry_at, task_id, owner))
            return changed.rowcount == 1

    def control(self, persona: str, task_id: str, action: str, *, publish_at: datetime | None = None,
                approval: dict | None = None,
                before_change: Callable[[str, str, dict], None] | None = None) -> dict:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE id=? AND persona=?', (task_id, persona)).fetchone()
            if row is None:
                raise KeyError(task_id)
            allowed = ('preparing', 'queued', 'failed') if action == 'cancel' else ('queued',) if action in ('publish-now', 'reschedule') else ('failed',)
            if row['state'] not in allowed:
                raise ValueError('当前状态需要先核对平台结果')
            body = json.loads(row['payload_json'])
            previous = dict(body)
            state = 'cancelled' if action == 'cancel' else 'preparing' if before_change is not None else 'queued'
            stamp = row['publish_at']
            if action != 'cancel':
                if not approval or approval.get('reviewed') is not True or not approval.get('approval_digest'):
                    raise ValueError('修改计划或重试需要重新审核')
                body.update({key: approval[key] for key in ('approval_digest', 'approval_expires_at') if key in approval})
                if publish_at is None or publish_at.tzinfo is None:
                    raise ValueError('新发布时间必须包含时区')
                stamp = publish_at.timestamp()
                body['scheduled_for'] = publish_at.isoformat() if action == 'reschedule' else ''
                body['retry'] = action == 'retry'
                attempt_key = f"{task_id}-{uuid.uuid4().hex}"
                body['idempotency_key'] = attempt_key
                body['run_id'] = attempt_key
            if before_change is not None:
                before_change(row['platform'], action, previous)
            db.execute("UPDATE tasks SET state=?,publish_at=?,payload_json=?,next_attempt=0,run_id=CASE WHEN ?!='cancel' THEN '' ELSE run_id END WHERE id=?",
                       (state, stamp, json.dumps(body), action, task_id))
            return self._task(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())
