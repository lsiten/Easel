"""Durable atomic batch claims and checkpoints sharing the generation database."""
from __future__ import annotations
from easel.queue_metrics import install_metrics, DRAFT_QUEUE
import hashlib
import time
import uuid
from easel.generation_draft_models import DraftCreated, DraftJob, DraftPage, DraftRequest, DraftStats
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.generation_queue import QueuePolicy, install_queue, next_job
from easel.publish_recovery import _owner_alive


def encoded(job: DraftJob) -> str:
    return job.model_dump_json(exclude={'status'})


class DraftStore:
    def __init__(self, base: GenerationStore) -> None:
        self.base = base
        with base.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS draft_batches(
                persona TEXT NOT NULL, request_key TEXT NOT NULL, digest TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(persona,request_key));
                CREATE TABLE IF NOT EXISTS draft_jobs(
                id TEXT PRIMARY KEY, persona TEXT NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                request_model TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'draft-generation', channel TEXT NOT NULL DEFAULT 'native',
                operation TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS draft_memory(
                persona TEXT NOT NULL, content_type TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(persona,content_type));''')

            install_queue(db, QueuePolicy('draft', base.outputs))
            install_metrics(db, DRAFT_QUEUE)

    def enqueue(self, persona: str, payload: DraftRequest, key: str) -> DraftCreated:
        digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest,body FROM draft_batches WHERE persona=? AND request_key=?', (persona, key)).fetchone()
            if row is not None:
                if row['digest'] != digest:
                    raise GenerationError('draft_request_conflict', 409)
                return DraftCreated.model_validate_json(row['body'])
            identifiers = []
            for _ in range(payload.quantity):
                stamp = time.time()
                job = DraftJob(id=uuid.uuid4().hex, persona=persona, request_key=key, request=payload,
                               createdAt=stamp, updatedAt=stamp)
                model = payload.imageModel if payload.kind == 'image-text' else payload.model if payload.kind == 'video' else payload.plannerModel
                db.execute('INSERT INTO draft_jobs(id,persona,state,request_model,operation,created_at,updated_at,body,priority) VALUES(?,?,?,?,?,?,?,?,?)',
                           (job.id, persona, job.state, model, payload.kind, stamp, stamp, encoded(job),
                            QueuePolicy('video' if payload.kind == 'video' else 'image', self.base.outputs).priority(model)))
                identifiers.append(job.id)
            result = DraftCreated(taskIds=tuple(identifiers))
            db.execute('INSERT INTO draft_batches VALUES(?,?,?,?)', (persona, key, digest, result.model_dump_json()))
            return result

    def get(self, persona: str, identifier: str) -> DraftJob:
        with self.base.connect() as db:
            row = db.execute('SELECT body FROM draft_jobs WHERE id=? AND persona=?', (identifier, persona)).fetchone()
            if row is None:
                raise GenerationError('draft_task_not_found', 404)
            return DraftJob.model_validate_json(row['body'])

    def list(self, persona: str, page: int = 1, size: int = 30) -> DraftPage:
        with self.base.connect() as db:
            total = db.execute('SELECT count(*) FROM draft_jobs WHERE persona=?', (persona,)).fetchone()[0]
            rows = db.execute('SELECT body FROM draft_jobs WHERE persona=? ORDER BY rowid DESC LIMIT ? OFFSET ?',
                              (persona, size, (page - 1) * size))
            items = tuple(DraftJob.model_validate_json(row['body']) for row in rows)
        return DraftPage(items=items, total=total, page=page, pageSize=size)

    def stats(self, persona: str) -> DraftStats:
        with self.base.connect() as db:
            count = db.execute("SELECT count(*) FROM draft_jobs WHERE persona=? AND state IN ('queued','running')", (persona,)).fetchone()[0]
        return DraftStats(generatingCount=count)

    def claim(self, owner: str, identifier: str = '') -> DraftJob | None:
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = next_job(db, QueuePolicy('draft', self.base.outputs), identifier)
            if row is None:
                return None
            job = DraftJob.model_validate_json(row['body']).model_copy(update={'state': 'running', 'updatedAt': time.time()})
            db.execute('UPDATE draft_jobs SET state=?,owner=?,body=?,updated_at=? WHERE id=?',
                       (job.state, owner, encoded(job), job.updatedAt, job.id))
            return job

    def save(self, job: DraftJob, owner: str) -> DraftJob:
        updated = job.model_copy(update={'updatedAt': time.time()})
        with self.base.connect() as db:
            changed = db.execute("UPDATE draft_jobs SET state=?,body=?,updated_at=? WHERE id=? AND persona=? AND owner=? AND state='running'",
                                 (updated.state, encoded(updated), updated.updatedAt, job.id, job.persona, owner)).rowcount
            if changed != 1:
                raise GenerationError('draft_worker_stale', 409)
        return updated

    def recover(self) -> int:
        recovered = 0
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT id,owner,body FROM draft_jobs WHERE state='running'").fetchall():
                if _owner_alive(row['owner']):
                    continue
                # Child calls use stable idempotency keys; ambiguous paid calls remain blocked in their own stores.
                job = DraftJob.model_validate_json(row['body']).model_copy(update={'state': 'queued', 'updatedAt': time.time()})
                db.execute("UPDATE draft_jobs SET state='queued',owner='',body=?,updated_at=? WHERE id=?", (encoded(job), job.updatedAt, job.id))
                recovered += 1
        return recovered
