"""Idempotent projection of successful generated video into its owned media group."""
import hashlib
from pathlib import Path
import sqlite3
import time

from easel.content_library import ContentLibrary
from easel.content_library_models import ContentError, ContentRecord, ContentScope, RecordInput
from easel.content_library_store import content_now
from easel.generation_video_models import VideoJob
from easel.generation_video_request import VideoGenerationRequest
from easel.generation_video_store import VideoStore


def validate_video_group(outputs: Path, payload: VideoGenerationRequest) -> None:
    if not payload.group_id:
        return
    group = ContentLibrary(outputs).get_group(ContentScope(persona=payload.persona, domain='media'), payload.group_id)
    if group.kind is not None and group.kind != 'video':
        raise ContentError('group_content_kind_mismatch')


def project_video(store: VideoStore, job: VideoJob) -> VideoJob:
    if job.state != 'succeeded' or job.asset is None or job.media_id or not isinstance(job.request, VideoGenerationRequest) or not job.request.group_id:
        return job
    library = ContentLibrary(store.generation.outputs)
    scope = ContentScope(persona=job.persona, domain='media')
    record_id = hashlib.sha256(('ai-video\0' + job.persona + '\0' + job.id).encode()).hexdigest()[:32]
    media_error = ''
    media_id = ''
    try:
        with library.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id FROM content_records WHERE id=? AND persona=? AND domain=?',
                                  (record_id, job.persona, 'media')).fetchone()
            if existing is None:
                thumbnail = next((asset.path for asset in job.additional_assets if asset.kind == 'image'), '')
                payload = RecordInput(group_id=job.request.group_id, title=job.request.prompt[:1000], kind='video',
                    path=job.asset.path, thumbnail_path=thumbnail, options={'generation_id': job.id, 'model': job.runtime_model})
                library._validate_input(db, scope, payload)
                stamp = content_now()
                record = ContentRecord(**payload.model_dump(), id=record_id, persona=job.persona, domain='media',
                                       created_at=stamp, updated_at=stamp)
                library.save_record(db, record)
            media_id = record_id
    except ContentError as error:
        media_error = error.code
    except (sqlite3.Error, OSError):
        media_error = 'video_media_projection_unavailable'
    with store.generation.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT body FROM video_jobs WHERE id=? AND persona=? AND state='succeeded'", (job.id, job.persona)).fetchone()
        if row is None:
            return job
        current = VideoJob.model_validate_json(row['body'])
        if current.media_id:
            return current
        result = current.model_copy(update={'media_id': media_id, 'media_error': media_error, 'updated_at': time.time()})
        db.execute('UPDATE video_jobs SET body=?,updated_at=? WHERE id=? AND persona=?',
                   (result.model_dump_json(), result.updated_at, result.id, result.persona))
    return result
