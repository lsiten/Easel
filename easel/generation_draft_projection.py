"""Idempotent owned media/material projection; usage counters survive recovery."""
from __future__ import annotations
import hashlib
from pathlib import Path
from urllib.parse import quote
from easel.content_library import ContentLibrary
from easel.content_library_models import ContentRecord, ContentScope, MediaReference, RecordInput
from easel.content_library_store import content_now
from easel.generation_draft_models import DraftJob, DraftResult
from easel.generation_draft_platforms import compatible_platforms, local_platforms
from easel.generation_models import GenerationError
from easel.generation_images import ImageGeneration
from easel.ownership import owned_media_path


def project_draft(outputs: Path, job: DraftJob) -> DraftResult:
    if job.plan is None or not job.media:
        raise GenerationError('draft_result_incomplete', 502)
    for media in job.media:
        _, path = owned_media_path(outputs, job.persona, media.asset.path)
        if media.asset.size != path.stat().st_size or media.asset.sha256 != ImageGeneration._hash(path):
            raise GenerationError('draft_media_changed', 409)
        expected = 'image' if job.request.kind == 'image-text' else 'video'
        if media.asset.kind != expected:
            raise GenerationError('draft_media_kind_invalid', 502)
    library = ContentLibrary(outputs)
    material_scope = ContentScope(persona=job.persona, domain='material')
    media_scope = ContentScope(persona=job.persona, domain='media')
    media_ids = []
    with library.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        media_group = library.ensure_default(db, media_scope)
        for position, media in enumerate(job.media):
            identifier = hashlib.sha256(f'draft-media\0{job.persona}\0{job.id}\0{position}'.encode()).hexdigest()[:32]
            payload = RecordInput(group_id=media_group.id, kind=media.asset.kind, path=media.asset.path,
                thumbnail_path=media.thumbnail, title=job.plan.title or job.request.prompt[:200] or 'Generated media',
                options={'draft_job_id': job.id, 'generation_id': media.generationId, 'sha256': media.asset.sha256,
                         'source': 'draft-generation', 'material_group_id': job.request.groupId})
            library._validate_input(db, media_scope, payload)
            if db.execute('SELECT id FROM content_records WHERE id=?', (identifier,)).fetchone() is None:
                stamp = content_now()
                library.save_record(db, ContentRecord(**payload.model_dump(), id=identifier, persona=job.persona,
                    domain='media', created_at=stamp, updated_at=stamp))
            media_ids.append(identifier)
        material_id = ''
        if job.request.draftType == 'draft':
            material_id = hashlib.sha256(f'draft-material\0{job.persona}\0{job.id}'.encode()).hexdigest()[:32]
            payload = RecordInput(group_id=job.request.groupId, kind='article' if job.request.kind == 'image-text' else 'video',
                title=job.plan.title, description=job.plan.description, topics=job.plan.topics,
                thumbnail_path=job.media[0].thumbnail, status='success', account_types=local_platforms(job.request.platforms if job.request.platforms is not None else compatible_platforms(job)),
                media=tuple(MediaReference(path=media.asset.path, media_id=identifier, thumbnail_path=media.thumbnail,
                    kind=media.asset.kind) for media, identifier in zip(job.media, media_ids)),
                options={'source': 'draft-generation', 'draft_job_id': job.id, 'generationParams': job.request.model_dump(mode='json')})
            library._validate_input(db, material_scope, payload)
            if db.execute('SELECT id FROM content_records WHERE id=?', (material_id,)).fetchone() is None:
                stamp = content_now()
                library.save_record(db, ContentRecord(**payload.model_dump(), id=material_id, persona=job.persona,
                    domain='material', created_at=stamp, updated_at=stamp))
    urls = tuple('/api/media/' + quote(media.asset.path, safe='/') for media in job.media)
    return DraftResult(materialId=material_id, mediaIds=tuple(media_ids), title=job.plan.title,
        description=job.plan.description, topics=job.plan.topics, plan=job.plan,
        imageUrls=urls if job.request.kind == 'image-text' else (),
        videoUrl=urls[0] if job.request.kind != 'image-text' else '',
        coverUrl='/api/media/' + quote(job.media[0].thumbnail, safe='/') if job.media[0].thumbnail else '')
