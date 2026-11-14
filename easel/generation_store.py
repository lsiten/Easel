"""Atomic claims and replay-safe storage for local AI creation jobs."""
from __future__ import annotations
from easel.queue_metrics import install_metrics, IMAGE_QUEUE

from collections.abc import Iterator
from contextlib import closing, contextmanager
import hashlib
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Literal

from easel.generation_models import (GeneratedAsset, GeneratedAssetPage, GenerationError, GenerationJob,
                                     GenerationPage, GenerationState, GenerationStatus, GenerationSummary, ImageRequest, ImageResult)
from easel.generation_queue import QueuePolicy, install_queue, next_job
from easel.publish_recovery import _owner_alive
from easel.ownership import MediaManifestEntry, build_media_manifest, owned_media_path


class GenerationStore:
    def __init__(self, outputs: Path) -> None:
        self.outputs = outputs.resolve()
        self.path = self.outputs / '_generation' / 'jobs.sqlite3'
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise GenerationError('generation_store_path_unsafe', 409)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS generation_jobs(
                id TEXT PRIMARY KEY, persona TEXT NOT NULL, request_key TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                request_model TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'image',
                channel TEXT NOT NULL DEFAULT 'new-api', operation TEXT NOT NULL DEFAULT 'generate',
                created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0,
                body TEXT NOT NULL, UNIQUE(persona,request_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS generation_assets(
                job_id TEXT NOT NULL, position INTEGER NOT NULL, persona TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(job_id,position))''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(generation_jobs)')}
            metadata = (('request_model', "TEXT NOT NULL DEFAULT ''"), ('kind', "TEXT NOT NULL DEFAULT 'image'"),
                        ('channel', "TEXT NOT NULL DEFAULT 'new-api'"), ('operation', "TEXT NOT NULL DEFAULT 'generate'"),
                        ('created_at', 'REAL NOT NULL DEFAULT 0'), ('updated_at', 'REAL NOT NULL DEFAULT 0'))
            for name, declaration in metadata:
                if name not in columns:
                    db.execute(f'ALTER TABLE generation_jobs ADD COLUMN {name} {declaration}')
            for row in db.execute("SELECT id,body FROM generation_jobs WHERE request_model=''"):
                legacy = GenerationJob.model_validate_json(row['body'])
                db.execute('''UPDATE generation_jobs SET request_model=?,kind=?,channel=?,operation=?,created_at=?,updated_at=? WHERE id=?''',
                           (legacy.request.model, legacy.kind, legacy.channel, legacy.request.operation,
                            legacy.created_at, legacy.updated_at, legacy.id))
            install_queue(db, QueuePolicy('image', self.outputs))
            install_metrics(db, IMAGE_QUEUE)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def enqueue(self, payload: ImageRequest, runtime_model: str = '', channel: str = '') -> GenerationJob:
        encoded = payload.model_dump_json()
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM generation_jobs WHERE persona=? AND request_key=?',
                             (payload.persona, payload.request_key)).fetchone()
            existing = self._replay(row, payload)
            if existing is not None:
                return existing
            stamp = time.time()
            paths = (*payload.images, *((payload.mask,) if payload.mask else ()))
            from easel.generation_inputs import external_reference
            local_paths = tuple(path for path in paths if not external_reference(path))
            inputs = build_media_manifest(self.outputs, payload.persona, local_paths)
            canonical = {path: owned_media_path(self.outputs, payload.persona, path)[1].relative_to(self.outputs).as_posix() for path in local_paths}
            saved_request = payload.model_copy(update={
                'image': tuple(canonical.get(path, path) for path in payload.images),
                'mask': canonical.get(payload.mask, payload.mask),
            })
            job = GenerationJob(id=uuid.uuid4().hex, persona=payload.persona, state='queued',
                                request=saved_request, inputs=inputs, runtime_model=runtime_model,
                                created_at=stamp, updated_at=stamp, channel=payload.channel or channel or 'new-api')
            db.execute('''INSERT INTO generation_jobs(id,persona,request_key,digest,state,request_model,
                kind,channel,operation,created_at,updated_at,body,priority) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (job.id, job.persona, payload.request_key, digest, job.state, payload.model, job.kind,
                        job.channel, payload.operation, job.created_at, job.updated_at, job.model_dump_json(),
                        QueuePolicy('image', self.outputs).priority(payload.model)))
            return job

    @staticmethod
    def _replay(row: sqlite3.Row | None, payload: ImageRequest) -> GenerationJob | None:
        if row is None:
            return None
        digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        if row['digest'] != digest:
            raise GenerationError('generation_request_conflict', 409)
        return GenerationJob.model_validate_json(row['body'])

    def replay(self, payload: ImageRequest) -> GenerationJob | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM generation_jobs WHERE persona=? AND request_key=?',
                             (payload.persona, payload.request_key)).fetchone()
        return self._replay(row, payload)

    def get(self, persona: str, identifier: str) -> GenerationJob:
        with self.connect() as db:
            row = db.execute('SELECT body FROM generation_jobs WHERE id=? AND persona=?',
                             (identifier, persona)).fetchone()
            if row is None:
                raise GenerationError('generation_not_found', 404)
            return GenerationJob.model_validate_json(row['body'])

    def list(self, persona: str, *, page: int = 1, page_size: int = 30,
             state: GenerationState | None = None, model: str = '', status: GenerationStatus | None = None,
             kind: str = '', channel: str = '') -> GenerationPage:
        from easel.generation_logs import GenerationLogQuery, generation_log_page
        query = GenerationLogQuery(persona=persona, page=page, page_size=page_size, state=state,
                                   model=model, status=status, kind=kind, channel=channel)
        with self.connect() as db:
            return generation_log_page(db, query)

    def claim(self, owner: str, identifier: str = '') -> GenerationJob | None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = next_job(db, QueuePolicy('image', self.outputs), identifier)
            if row is None:
                return None
            job = GenerationJob.model_validate_json(row['body']).model_copy(update={'state': 'running', 'updated_at': time.time()})
            db.execute('UPDATE generation_jobs SET state=?,owner=?,body=?,updated_at=? WHERE id=?',
                       (job.state, owner, job.model_dump_json(), job.updated_at, job.id))
            return job

    def finish(self, job: GenerationJob, owner: str, state: Literal['succeeded', 'failed', 'unknown'], *,
               assets: tuple[GeneratedAsset, ...] = (), response: ImageResult | None = None, error: str = '') -> GenerationJob:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT body FROM generation_jobs WHERE id=? AND state='running' AND owner=?", (job.id, owner)).fetchone()
            if row is None:
                raise GenerationError('generation_worker_stale', 409)
            current = GenerationJob.model_validate_json(row['body'])
            result = current.model_copy(update={'state': state, 'assets': assets, 'response': response,
                                                'error': error, 'updated_at': time.time()})
            changed = db.execute("UPDATE generation_jobs SET state=?,body=?,updated_at=? WHERE id=? AND state='running' AND owner=?",
                                 (state, result.model_dump_json(), result.updated_at, job.id, owner)).rowcount
            if changed != 1:
                raise GenerationError('generation_worker_stale', 409)
            for index, asset in enumerate(assets):
                db.execute('INSERT INTO generation_assets VALUES(?,?,?,?)', (job.id, index, current.persona, asset.model_dump_json()))
        return result

    def record_inputs(self, job: GenerationJob, owner: str, inputs: tuple[MediaManifestEntry, ...]) -> None:
        updated = job.model_copy(update={'inputs': (*job.inputs, *inputs), 'updated_at': time.time()})
        with self.connect() as db:
            changed = db.execute("UPDATE generation_jobs SET body=?,updated_at=? WHERE id=? AND state='running' AND owner=?",
                                 (updated.model_dump_json(), updated.updated_at, job.id, owner)).rowcount
            if changed != 1:
                raise GenerationError('generation_worker_stale', 409)

    def assets(self, persona: str, page: int = 1, page_size: int = 30) -> GeneratedAssetPage:
        if page < 1 or not 1 <= page_size <= 200:
            raise GenerationError('generation_pagination_invalid')
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM generation_assets WHERE persona=?', (persona,)).fetchone()[0]
            rows = db.execute('SELECT body FROM generation_assets WHERE persona=? ORDER BY rowid DESC LIMIT ? OFFSET ?',
                              (persona, page_size, (page - 1) * page_size))
            items = tuple(GeneratedAsset.model_validate_json(row['body']) for row in rows)
        return GeneratedAssetPage(items=items, total=total, page=page, page_size=page_size)

    def recover(self) -> int:
        """Preserve interrupted paid calls as unknown without automatic resubmission."""
        count = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM generation_jobs WHERE state='running'").fetchall():
                if _owner_alive(row['owner']):
                    continue
                job = GenerationJob.model_validate_json(row['body']).model_copy(update={
                    'state': 'unknown', 'error': 'generation_worker_interrupted', 'updated_at': time.time()})
                db.execute("UPDATE generation_jobs SET state='unknown',body=?,updated_at=? WHERE id=?",
                           (job.model_dump_json(), job.updated_at, job.id))
                count += 1
        return count
