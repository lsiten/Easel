"""Transactional DirectEdit claims and durable native receipts."""
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import time
import uuid
from easel.generation_direct_edit_models import DirectEditJob, DirectEditRequest
from easel.generation_models import GenerationError
from easel.publish_recovery import _owner_alive, worker_owner
from easel.generation_store import GenerationStore
from easel.queue_metrics import install_metrics, DIRECT_EDIT_QUEUE


class DirectEditStore:
    def __init__(self, base: GenerationStore) -> None:
        self.base = base
        with base.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS direct_edit_jobs(
                id TEXT PRIMARY KEY, persona TEXT NOT NULL, request_key TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, channel TEXT NOT NULL,
                request_model TEXT NOT NULL, operation TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, body TEXT NOT NULL,
                UNIQUE(persona,request_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS direct_edit_poll_claims(
                job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, attempted_at REAL NOT NULL)''')

            install_metrics(db, DIRECT_EDIT_QUEUE)

    def replay(self, request: DirectEditRequest) -> DirectEditJob | None:
        with self.base.connect() as db:
            row = db.execute('SELECT digest,body FROM direct_edit_jobs WHERE persona=? AND request_key=?',
                             (request.persona, request.request_key)).fetchone()
        if row is None:
            return None
        if row['digest'] != hashlib.sha256(request.model_dump_json().encode()).hexdigest():
            raise GenerationError('direct_edit_request_conflict', 409)
        return DirectEditJob.model_validate_json(row['body'])

    def claim(self, request: DirectEditRequest, space: str) -> tuple[DirectEditJob, bool]:
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM direct_edit_jobs WHERE persona=? AND request_key=?',
                             (request.persona, request.request_key)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise GenerationError('direct_edit_request_conflict', 409)
                return DirectEditJob.model_validate_json(row['body']), False
            stamp = time.time()
            job = DirectEditJob(id=uuid.uuid4().hex, persona=request.persona, request=request,
                                space=space, created_at=stamp, updated_at=stamp)
            db.execute('INSERT INTO direct_edit_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (job.id, job.persona, request.request_key, digest, job.state, job.channel,
                        job.request_model, job.operation, stamp, stamp, job.model_dump_json()))
            return job, True

    def save(self, job: DirectEditJob, owner: str = '') -> DirectEditJob:
        updated = job.model_copy(update={'updated_at': time.time()})
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if owner and db.execute('SELECT 1 FROM direct_edit_poll_claims WHERE job_id=? AND owner=?',
                                    (job.id, owner)).fetchone() is None:
                raise GenerationError('direct_edit_poll_worker_stale', 409)
            current = db.execute('SELECT body FROM direct_edit_jobs WHERE id=? AND persona=?',
                                 (job.id, job.persona)).fetchone()
            if current is None:
                raise GenerationError('direct_edit_not_found', 404)
            stored = DirectEditJob.model_validate_json(current['body'])
            if stored.state in ('succeeded', 'failed'):
                return stored
            db.execute('UPDATE direct_edit_jobs SET state=?,updated_at=?,body=? WHERE id=? AND persona=?',
                       (updated.state, updated.updated_at, updated.model_dump_json(), job.id, job.persona))
            for index, asset in enumerate(updated.assets):
                db.execute('INSERT OR REPLACE INTO generation_assets VALUES(?,?,?,?)',
                           (job.id, index, job.persona, asset.model_dump_json()))
        return updated

    def get(self, persona: str, identifier: str) -> DirectEditJob:
        with self.base.connect() as db:
            row = db.execute('SELECT body FROM direct_edit_jobs WHERE id=? AND persona=?', (identifier, persona)).fetchone()
        if row is None:
            raise GenerationError('direct_edit_not_found', 404)
        return DirectEditJob.model_validate_json(row['body'])

    def candidates(self, limit: int = 100) -> tuple[tuple[str, str], ...]:
        with self.base.connect() as db:
            rows = db.execute('''SELECT j.persona,j.id FROM direct_edit_jobs j
                LEFT JOIN direct_edit_poll_claims p ON p.job_id=j.id
                WHERE j.state='running' ORDER BY COALESCE(p.attempted_at,0),j.created_at LIMIT ?''', (limit,)).fetchall()
        return tuple((row['persona'], row['id']) for row in rows)

    @contextmanager
    def poll(self, persona: str, identifier: str) -> Iterator[tuple[DirectEditJob, str]]:
        """Serialize archival across GET/background workers, recovering only dead owners."""
        owner = ''
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM direct_edit_jobs WHERE id=? AND persona=?', (identifier, persona)).fetchone()
            if row is None:
                raise GenerationError('direct_edit_not_found', 404)
            job = DirectEditJob.model_validate_json(row['body'])
            previous = db.execute('SELECT owner FROM direct_edit_poll_claims WHERE job_id=?', (identifier,)).fetchone()
            active = previous is not None and bool(previous['owner']) and _owner_alive(previous['owner'])
            if job.provider_id and job.state in ('running', 'unknown') and not active:
                owner = worker_owner()
                db.execute('INSERT OR REPLACE INTO direct_edit_poll_claims VALUES(?,?,?)', (identifier, owner, time.time()))
        try:
            yield job, owner
        finally:
            if owner:
                with self.base.connect() as db:
                    db.execute("UPDATE direct_edit_poll_claims SET owner='' WHERE job_id=? AND owner=?", (identifier, owner))
