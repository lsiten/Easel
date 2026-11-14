"""Native video queue sharing the protected generation SQLite database."""
from __future__ import annotations
from easel.queue_metrics import install_metrics, VIDEO_QUEUE

import hashlib
import time
import uuid
from typing_extensions import assert_never

from easel.generation_models import GeneratedAsset, GenerationError
from easel.generation_store import GenerationStore
from easel.generation_video_models import OpenAIVideoRequest, VideoJob, VideoPage, VideoRequest, VideoState, VideoSummary, VideoTarget
from easel.generation_volcengine_models import VolcengineRequest
from easel.ownership import build_media_manifest, owned_media_path
from easel.generation_video_request import VideoGenerationRequest
from easel.generation_queue import QueuePolicy, install_queue, next_job
from easel.publish_recovery import _owner_alive


class VideoStore:
    def __init__(self, generation: GenerationStore) -> None:
        self.generation = generation
        with generation.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS video_jobs(
                id TEXT PRIMARY KEY, persona TEXT NOT NULL, request_key TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL, operation TEXT NOT NULL, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, ready_at REAL NOT NULL DEFAULT 0,
                body TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'openai',
                request_model TEXT NOT NULL DEFAULT '', UNIQUE(persona,request_key))''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(video_jobs)')}
            if 'channel' not in columns:
                db.execute("ALTER TABLE video_jobs ADD COLUMN channel TEXT NOT NULL DEFAULT 'openai'")
            if 'request_model' not in columns:
                db.execute("ALTER TABLE video_jobs ADD COLUMN request_model TEXT NOT NULL DEFAULT ''")
            for row in db.execute("SELECT body FROM video_jobs WHERE request_model=''").fetchall():
                job = VideoJob.model_validate_json(row['body'])
                db.execute('UPDATE video_jobs SET request_model=? WHERE id=?', (job.request.model or job.runtime_model, job.id))

            install_queue(db, QueuePolicy('video', generation.outputs))
            install_metrics(db, VIDEO_QUEUE)

    def replay(self, payload: VideoRequest, source_id: str = '') -> VideoJob | None:
        with self.generation.connect() as db:
            row = db.execute('SELECT digest,body FROM video_jobs WHERE persona=? AND request_key=?',
                             (payload.persona, payload.request_key)).fetchone()
        if row is None:
            return None
        if row['digest'] != self.digest(payload, source_id):
            raise GenerationError('generation_request_conflict', 409)
        return VideoJob.model_validate_json(row['body'])

    @staticmethod
    def digest(payload: VideoRequest, source_id: str) -> str:
        return hashlib.sha256((source_id + '\0' + payload.model_dump_json()).encode()).hexdigest()

    def enqueue(self, payload: VideoRequest, target: VideoTarget) -> VideoJob:
        with self.generation.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.replay(payload, target.source_id)
            if existing is not None:
                return existing
            from easel.generation_inputs import external_reference
            match payload:
                case OpenAIVideoRequest():
                    paths = (payload.input_reference,) if payload.input_reference and not external_reference(payload.input_reference) else ()
                case VolcengineRequest():
                    from easel.generation_volcengine_payload import volcengine_local_paths
                    paths = volcengine_local_paths(payload)
                case VideoGenerationRequest():
                    paths = tuple(item.value for item in payload.references() if not external_reference(item.value))
                case unreachable:
                    assert_never(unreachable)
            inputs = build_media_manifest(self.generation.outputs, payload.persona, paths)
            match payload:
                case OpenAIVideoRequest():
                    saved_request = payload.model_copy(update={'input_reference': inputs[0].relative_path}) if inputs else payload
                case VolcengineRequest():
                    from easel.generation_volcengine_payload import canonical_volcengine
                    saved_request = canonical_volcengine(payload, self.generation.outputs)
                case VideoGenerationRequest():
                    canonical = {path: owned_media_path(self.generation.outputs, payload.persona, path)[1].relative_to(self.generation.outputs).as_posix() for path in paths}
                    saved_request = payload.replace_references(canonical)
                    if target.effective_request is not None:
                        target = target.model_copy(update={'effective_request': target.effective_request.replace_references(canonical)})
                case unreachable:
                    assert_never(unreachable)
            stamp = time.time()
            job = VideoJob(id=uuid.uuid4().hex, persona=payload.persona, state='queued', request=saved_request,
                           inputs=inputs, base_url=target.base, runtime_model=target.model, source_id=target.source_id, channel=target.channel,
                           created_at=stamp, updated_at=stamp, effective_request=target.effective_request)
            db.execute('''INSERT INTO video_jobs(id,persona,request_key,digest,state,model,operation,created_at,updated_at,body,channel,request_model,priority)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (job.id, job.persona, payload.request_key, self.digest(payload, target.source_id),
                job.state, target.model, 'remix' if target.source_id else 'create', stamp, stamp, job.model_dump_json(), target.channel, payload.model or target.model,
                QueuePolicy('video', self.generation.outputs).priority(payload.model or target.model)))
        return job

    def get(self, persona: str, identifier: str, channel: str = '') -> VideoJob:
        with self.generation.connect() as db:
            row = db.execute("SELECT body FROM video_jobs WHERE id=? AND persona=? AND (?='' OR channel=?)", (identifier, persona, channel, channel)).fetchone()
        if row is None:
            raise GenerationError('video_not_found', 404)
        return VideoJob.model_validate_json(row['body'])

    def claim(self, owner: str, identifier: str = '') -> VideoJob | None:
        with self.generation.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = next_job(db, QueuePolicy('video', self.generation.outputs), identifier)
            if row is None:
                return None
            job = VideoJob.model_validate_json(row['body']).model_copy(update={'state': 'submitting', 'updated_at': time.time()})
            db.execute("UPDATE video_jobs SET state='submitting',owner=?,body=?,updated_at=? WHERE id=?",
                       (owner, job.model_dump_json(), job.updated_at, job.id))
        return job

    def save(self, job: VideoJob, owner: str, state: VideoState, *, asset: GeneratedAsset | None = None) -> VideoJob:
        result = job.model_copy(update={'state': state, 'updated_at': time.time(), 'asset': asset or job.asset})
        with self.generation.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('''UPDATE video_jobs SET state=?,body=?,updated_at=?,ready_at=?
                WHERE id=? AND state='submitting' AND owner=?''',
                (state, result.model_dump_json(), result.updated_at, result.updated_at + (2 if state == 'waiting' else 0),
                 result.id, owner)).rowcount
            if changed != 1:
                raise GenerationError('generation_worker_stale', 409)
            if asset is not None:
                for position, item in enumerate((asset, *job.additional_assets)):
                    db.execute('INSERT INTO generation_assets VALUES(?,?,?,?)', (job.id, position, job.persona, item.model_dump_json()))
        return result

    def list(self, persona: str, page: int = 1, page_size: int = 30, *, channel: str = '') -> VideoPage:
        if page < 1 or not 1 <= page_size <= 1000:
            raise GenerationError('generation_pagination_invalid')
        with self.generation.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM video_jobs WHERE persona=? AND (?='' OR channel=?)", (persona, channel, channel)).fetchone()[0]
            rows = db.execute('''SELECT id,persona,state,model,channel,operation,created_at,updated_at FROM video_jobs
                WHERE persona=? AND (?='' OR channel=?) ORDER BY rowid DESC LIMIT ? OFFSET ?''', (persona, channel, channel, page_size, (page - 1) * page_size))
            items = tuple(VideoSummary.model_validate(dict(row)) for row in rows)
        return VideoPage(items=items, total=total, page=page, page_size=page_size)

    def recover(self) -> int:
        count = 0
        with self.generation.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT owner,body FROM video_jobs WHERE state='submitting'").fetchall():
                if _owner_alive(row['owner']):
                    continue
                job = VideoJob.model_validate_json(row['body'])
                state = 'waiting' if job.provider_id else 'unknown'
                result = job.model_copy(update={'state': state, 'error': 'generation_worker_interrupted', 'updated_at': time.time()})
                db.execute('UPDATE video_jobs SET state=?,body=?,updated_at=?,ready_at=0 WHERE id=?',
                           (state, result.model_dump_json(), result.updated_at, job.id))
                count += 1
        return count
