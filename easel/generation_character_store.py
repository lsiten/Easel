"""Persistent owner-scoped character receipts and paid request claims."""
import hashlib
import time
import uuid

from easel.generation_character_models import CharacterJob, CharacterRequest
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.queue_metrics import install_metrics, CHARACTER_QUEUE


class CharacterStore:
    def __init__(self, base: GenerationStore) -> None:
        self.base = base
        with base.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS character_jobs(
                id TEXT PRIMARY KEY,persona TEXT NOT NULL,request_key TEXT NOT NULL,digest TEXT NOT NULL,
                state TEXT NOT NULL,channel TEXT NOT NULL,request_model TEXT NOT NULL,operation TEXT NOT NULL,
                created_at REAL NOT NULL,updated_at REAL NOT NULL,body TEXT NOT NULL,UNIQUE(persona,request_key))''')

            install_metrics(db, CHARACTER_QUEUE)

    def replay(self, request: CharacterRequest) -> CharacterJob | None:
        with self.base.connect() as db:
            row = db.execute('SELECT body,digest FROM character_jobs WHERE persona=? AND request_key=?',
                             (request.persona, request.request_key)).fetchone()
        if row is None:
            return None
        if row['digest'] != hashlib.sha256(request.model_dump_json().encode()).hexdigest():
            raise GenerationError('character_request_conflict', 409)
        return CharacterJob.model_validate_json(row['body'])

    def claim(self, request: CharacterRequest) -> tuple[CharacterJob, bool]:
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.replay(request)
            if existing is not None:
                return existing, False
            stamp = time.time()
            job = CharacterJob(id=uuid.uuid4().hex, persona=request.persona, request=request,
                               created_at=stamp, updated_at=stamp)
            db.execute('INSERT INTO character_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (job.id, job.persona, request.request_key, hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
                        job.state, 'openai', 'sora-2-character', 'create', stamp, stamp, job.model_dump_json()))
            return job, True

    def save(self, job: CharacterJob) -> CharacterJob:
        updated = job.model_copy(update={'updated_at': time.time()})
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM character_jobs WHERE id=? AND persona=?', (job.id, job.persona)).fetchone()
            if row is None:
                raise GenerationError('character_not_found', 404)
            previous = CharacterJob.model_validate_json(row['body'])
            if previous.state in ('succeeded', 'failed'):
                return previous
            db.execute('UPDATE character_jobs SET state=?,updated_at=?,body=? WHERE id=?',
                       (updated.state, updated.updated_at, updated.model_dump_json(), updated.id))
            return updated

    def get(self, persona: str, identifier: str) -> CharacterJob:
        with self.base.connect() as db:
            row = db.execute('SELECT body FROM character_jobs WHERE id=? AND persona=?', (identifier, persona)).fetchone()
        if row is None:
            raise GenerationError('character_not_found', 404)
        return CharacterJob.model_validate_json(row['body'])
