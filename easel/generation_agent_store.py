"""Owner-scoped, transactionally claimed agent tasks in GenerationStore SQLite."""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from easel.generation_agent_models import (AgentEvent, AgentRun, AgentSnapshot, AgentTask, CreateTask,
    PublicShare, TaskPage, TaskUpdate)
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.queue_metrics import install_metrics, AGENT_QUEUE


class AgentStore:
    def __init__(self, store: GenerationStore) -> None:
        self.store = store
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS agent_tasks(
                id TEXT PRIMARY KEY, persona TEXT NOT NULL, body TEXT NOT NULL,
                run TEXT NOT NULL, turn_id TEXT NOT NULL, status TEXT NOT NULL,
                share_hash TEXT, share_expiry REAL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agent_events(
                task_id TEXT NOT NULL, uuid TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(task_id,uuid));''')
            install_metrics(db, AGENT_QUEUE)

    def get(self, persona: str, task_id: str) -> AgentTask:
        with self.store.connect() as db:
            row = db.execute('SELECT body FROM agent_tasks WHERE id=? AND persona=? AND deleted=0',
                             (task_id, persona)).fetchone()
            if row is None:
                raise GenerationError('agent_task_not_found', 404)
            messages = tuple(AgentEvent.model_validate_json(event['body']) for event in db.execute(
                'SELECT body FROM agent_events WHERE task_id=? ORDER BY rowid', (task_id,)))
            return AgentTask.model_validate_json(row['body']).model_copy(update={'messages': messages})

    def run(self, persona: str, task_id: str) -> AgentRun:
        with self.store.connect() as db:
            row = db.execute('SELECT run FROM agent_tasks WHERE id=? AND persona=? AND deleted=0',
                             (task_id, persona)).fetchone()
            if row is None:
                raise GenerationError('agent_task_not_found', 404)
            return AgentRun.model_validate_json(row['run'])

    def claim(self, persona: str, payload: CreateTask, *, external: AgentRun | None = None) -> AgentRun:
        task_id = external.task_id if external else payload.taskId or uuid.uuid4().hex
        run = external or AgentRun(persona=persona, task_id=task_id, turn_id=uuid.uuid4().hex,
                       prompt=payload.prompt, model=payload.model)
        stamp = time.time()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task_id,)).fetchone()
            if payload.taskId:
                if row is None or row['persona'] != persona or row['deleted']:
                    raise GenerationError('agent_task_not_found', 404)
                if row['status'] == 'running':
                    raise GenerationError('agent_task_running', 409)
                previous = AgentRun.model_validate_json(row['run'])
                run = run.model_copy(update={'session_id': previous.session_id})
                task = AgentTask.model_validate_json(row['body']).model_copy(update={'status': 'running', 'updatedAt': stamp})
                db.execute('UPDATE agent_tasks SET body=?,run=?,turn_id=?,status=? WHERE id=?',
                           (task.model_dump_json(), run.model_dump_json(), run.turn_id, 'running', task_id))
            else:
                text = payload.prompt if isinstance(payload.prompt, str) else next(
                    (block.text for block in payload.prompt if block.type == 'text'), 'New task')
                task = AgentTask(id=task_id, userId=persona, title=text[:200], status='running',
                                 createdAt=stamp, updatedAt=stamp)
                db.execute('INSERT INTO agent_tasks(id,persona,body,run,turn_id,status) VALUES(?,?,?,?,?,?)',
                           (task_id, persona, task.model_dump_json(), run.model_dump_json(), run.turn_id, 'running'))
            event = AgentEvent(uuid=run.turn_id + ':user', type='user', data=payload.model_dump(mode='json')['prompt'])
            db.execute('INSERT INTO agent_events VALUES(?,?,?)', (task_id, event.uuid, event.model_dump_json()))
        return run

    def ingest(self, run: AgentRun, snapshot: AgentSnapshot) -> None:
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM agent_tasks WHERE id=? AND persona=? AND turn_id=? AND deleted=0 AND status=?',
                             (run.task_id, run.persona, run.turn_id, 'running')).fetchone()
            if row is None:
                return
            for event in snapshot.events:
                db.execute('INSERT OR IGNORE INTO agent_events VALUES(?,?,?)',
                           (run.task_id, event.uuid, event.model_dump_json()))
            task = AgentTask.model_validate_json(row['body']).model_copy(update={'status': snapshot.status, 'updatedAt': time.time()})
            db.execute('UPDATE agent_tasks SET status=?,body=? WHERE id=?',
                       (task.status, task.model_dump_json(), task.id))

    def update(self, task: AgentTask, changes: TaskUpdate) -> None:
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM agent_tasks WHERE id=? AND persona=? AND deleted=0',
                             (task.id, task.userId)).fetchone()
            if row is None:
                raise GenerationError('agent_task_not_found', 404)
            current = AgentTask.model_validate_json(row['body'])
            if 'rating' in changes.model_fields_set and current.status not in ('completed', 'requires_action'):
                raise GenerationError('agent_task_status_invalid', 409)
            updated = current.model_copy(update={**changes.model_dump(exclude_unset=True), 'updatedAt': time.time()})
            db.execute('UPDATE agent_tasks SET body=? WHERE id=?', (updated.model_dump_json(), task.id))

    def list(self, persona: str, *, page: int, size: int, keyword: str, favorite: bool) -> TaskPage:
        with self.store.connect() as db:
            rows = db.execute('SELECT id FROM agent_tasks WHERE persona=? AND deleted=0 ORDER BY rowid DESC', (persona,)).fetchall()
        tasks = [self.get(persona, row['id']) for row in rows]
        matching = [task.model_copy(update={'messages': ()}) for task in tasks
                    if (not favorite or task.favoritedAt is not None) and
                    (not keyword or keyword.casefold() in (task.title + ' ' + ' '.join(event.model_dump_json() for event in task.messages)).casefold())]
        return TaskPage(items=tuple(matching[(page-1)*size:page*size]), total=len(matching), page=page, pageSize=size)

    def share(self, task: AgentTask, ttl: int) -> PublicShare:
        token = secrets.token_urlsafe(32)
        expiry = time.time() + ttl
        with self.store.connect() as db:
            changed = db.execute('UPDATE agent_tasks SET share_hash=?,share_expiry=? WHERE id=? AND persona=? AND deleted=0',
                                 (hashlib.sha256(token.encode()).hexdigest(), expiry, task.id, task.userId)).rowcount
            if changed != 1:
                raise GenerationError('agent_task_not_found', 404)
        return PublicShare(token=token, expiresAt=expiry)

    def shared(self, token: str) -> AgentTask:
        with self.store.connect() as db:
            row = db.execute('SELECT id,persona FROM agent_tasks WHERE share_hash=? AND share_expiry>? AND deleted=0',
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if row is None:
            raise GenerationError('agent_task_not_found', 404)
        return self.get(row['persona'], row['id'])

    def delete(self, run: AgentRun) -> AgentRun:
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT run FROM agent_tasks WHERE id=? AND persona=? AND deleted=0',
                             (run.task_id, run.persona)).fetchone()
            if row is None:
                raise GenerationError('agent_task_not_found', 404)
            current_run = AgentRun.model_validate_json(row['run'])
            db.execute('UPDATE agent_tasks SET deleted=1,share_hash=NULL,share_expiry=NULL WHERE id=? AND persona=?',
                       (run.task_id, run.persona))
            db.execute('DELETE FROM agent_events WHERE task_id=?', (run.task_id,))
            return current_run
