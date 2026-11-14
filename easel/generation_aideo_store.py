"""Transactional Aideo claims; native receipts survive process restarts."""
import hashlib
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from easel.generation_store import GenerationStore
from easel.queue_metrics import install_metrics, AIDEO_QUEUE
from easel.generation_models import GenerationError
from easel.generation_aideo_models import AideoJob, AideoRequest, WorkflowRequest, StyleTransferRequest, DramaRecapRequest
from easel.publish_recovery import _owner_alive, worker_owner


@dataclass(frozen=True, slots=True)
class AideoPoll:
    job: AideoJob
    owner: str = ''


class AideoStore:
    def __init__(self, base: GenerationStore) -> None:
        self.base = base
        with base.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS aideo_jobs(
                id TEXT PRIMARY KEY, persona TEXT NOT NULL, request_key TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, channel TEXT NOT NULL,
                request_model TEXT NOT NULL, operation TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, body TEXT NOT NULL,
                UNIQUE(persona,request_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS aideo_poll_claims(
                job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, attempted_at REAL NOT NULL)''')

            install_metrics(db, AIDEO_QUEUE)

    def replay(self, request: AideoRequest | WorkflowRequest) -> AideoJob | None:
        """Resolve a paid intent before checking credentials for a new submission."""
        with self.base.connect() as db:
            row = db.execute('SELECT digest,body FROM aideo_jobs WHERE persona=? AND request_key=?',
                             (request.persona, request.request_key)).fetchone()
        if row is None:
            return None
        if row['digest'] != hashlib.sha256(request.model_dump_json().encode()).hexdigest():
            raise GenerationError('aideo_request_conflict', 409)
        return AideoJob.model_validate_json(row['body'])

    def claim(self, request: AideoRequest | WorkflowRequest, space: str) -> tuple[AideoJob, bool]:
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM aideo_jobs WHERE persona=? AND request_key=?',
                             (request.persona, request.request_key)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise GenerationError('aideo_request_conflict', 409)
                return AideoJob.model_validate_json(row['body']), False
            stamp = time.time()
            job = AideoJob(id=uuid.uuid4().hex, persona=request.persona, request=request,
                           space=space, created_at=stamp, updated_at=stamp,
                           state='unknown', error='aideo_submission_in_progress_or_interrupted',
                           request_model='video-style-transfer' if isinstance(request, StyleTransferRequest) else 'drama-recap' if isinstance(request, DramaRecapRequest) else 'aideo',
                           channel='style-transfer' if isinstance(request, StyleTransferRequest) else 'volcengine')
            db.execute('INSERT INTO aideo_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (job.id, job.persona, request.request_key, digest, job.state, job.channel,
                        job.request_model, job.operation, stamp, stamp, job.model_dump_json()))
            return job, True

    def save(self, job: AideoJob, owner: str = '') -> AideoJob:
        job = job.model_copy(update={'updated_at': time.time()})
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if owner and db.execute('SELECT 1 FROM aideo_poll_claims WHERE job_id=? AND owner=?',
                                    (job.id, owner)).fetchone() is None:
                raise GenerationError('aideo_poll_worker_stale', 409)
            db.execute('UPDATE aideo_jobs SET state=?,updated_at=?,body=? WHERE id=? AND persona=?',
                       (job.state, job.updated_at, job.model_dump_json(), job.id, job.persona))
            for index, asset in enumerate(job.assets):
                db.execute('INSERT OR REPLACE INTO generation_assets VALUES(?,?,?,?)',
                           (job.id, index, job.persona, asset.model_dump_json()))
        return job

    def pending(self, limit: int = 100) -> tuple[AideoJob, ...]:
        """Bound each pass and rotate attempted jobs so a long queue cannot starve."""
        with self.base.connect() as db:
            rows = db.execute('''SELECT j.body FROM aideo_jobs j
                LEFT JOIN aideo_poll_claims p ON p.job_id=j.id
                WHERE j.state IN ('running','unknown') AND json_extract(j.body,'$.provider_id')!=''
                ORDER BY COALESCE(p.attempted_at,0),j.created_at LIMIT ?''', (limit,)).fetchall()
        return tuple(AideoJob.model_validate_json(row['body']) for row in rows)

    @contextmanager
    def poll(self, persona: str, identifier: str) -> Iterator[AideoPoll]:
        """Fence GET and background work until completion or process death.

        A live local worker keeps its claim even during a slow archive. Expiring a
        timed lease could let a second worker overwrite files still being written.
        """
        owner = ''
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM aideo_jobs WHERE id=? AND persona=?', (identifier, persona)).fetchone()
            if row is None:
                raise GenerationError('aideo_not_found', 404)
            job = AideoJob.model_validate_json(row['body'])
            previous = db.execute('SELECT owner FROM aideo_poll_claims WHERE job_id=?', (identifier,)).fetchone()
            active = previous is not None and bool(previous['owner']) and _owner_alive(previous['owner'])
            if job.provider_id and job.state in ('running', 'unknown') and not active:
                owner = worker_owner()
                db.execute('INSERT OR REPLACE INTO aideo_poll_claims VALUES(?,?,?)', (identifier, owner, time.time()))
        try:
            yield AideoPoll(job, owner)
        finally:
            if owner:
                with self.base.connect() as db:
                    db.execute("UPDATE aideo_poll_claims SET owner='' WHERE job_id=? AND owner=?", (identifier, owner))

    def get(self, persona: str, identifier: str) -> AideoJob:
        with self.base.connect() as db:
            row = db.execute('SELECT body FROM aideo_jobs WHERE id=? AND persona=?', (identifier, persona)).fetchone()
        if row is None:
            raise GenerationError('aideo_not_found', 404)
        return AideoJob.model_validate_json(row['body'])

    def page(self, persona: str, pagination: tuple[int, int]) -> tuple[tuple[AideoJob, ...], int]:
        page, size = pagination
        with self.base.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM aideo_jobs WHERE persona=?', (persona,)).fetchone()[0]
            rows = db.execute('SELECT body FROM aideo_jobs WHERE persona=? ORDER BY rowid DESC LIMIT ? OFFSET ?',
                              (persona, size, (page - 1) * size)).fetchall()
        return tuple(AideoJob.model_validate_json(row['body']) for row in rows), total
