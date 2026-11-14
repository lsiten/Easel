"""Owner-scoped stable preference summaries from recent successful draft history."""
from __future__ import annotations
import json
import time
import uuid
from datetime import datetime, timezone
from easel.content_library import ContentLibrary
from easel.content_library_models import ContentFilter, ContentScope
from easel.generation_draft_models import (ContentType, DraftJob, DraftMemory, DraftRuntime, MemoryItem,
    MemoryRegenerate, MemoryRun)
from easel.generation_draft_store import DraftStore
from easel.generation_models import GenerationError


class DraftMemories:
    def __init__(self, store: DraftStore, runtime: DraftRuntime) -> None:
        self.store, self.runtime = store, runtime

    def list(self, persona: str, kind: ContentType | None = None) -> tuple[DraftMemory, ...]:
        with self.store.base.connect() as db:
            rows = db.execute('SELECT body FROM draft_memory WHERE persona=? AND (? IS NULL OR content_type=?)', (persona, kind, kind))
            return tuple(DraftMemory.model_validate_json(row['body']) for row in rows)

    def delete(self, persona: str, identifier: str) -> DraftMemory:
        with self.store.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT body FROM draft_memory WHERE persona=?', (persona,)).fetchall():
                memory = DraftMemory.model_validate_json(row['body'])
                items = tuple(item for item in memory.items if item.id != identifier)
                if len(items) != len(memory.items):
                    updated = memory.model_copy(update={'items': items, 'updatedAt': time.time()})
                    db.execute('UPDATE draft_memory SET body=? WHERE persona=? AND content_type=?',
                               (updated.model_dump_json(), persona, memory.contentType))
                    return updated
        raise GenerationError('draft_memory_not_found', 404)

    async def regenerate(self, persona: str, payload: MemoryRegenerate) -> DraftMemory:
        since = time.time() - 90 * 86400
        with self.store.base.connect() as db:
            rows = db.execute("SELECT body FROM draft_jobs WHERE persona=? AND state='succeeded' AND operation=? AND created_at>? ORDER BY created_at DESC LIMIT 25",
                              (persona, payload.contentType, since))
            jobs = tuple(DraftJob.model_validate_json(row['body']) for row in rows)
        library = ContentLibrary(self.store.base.outputs)
        scope = ContentScope(persona=persona, domain='material')
        with library.connect() as db:
            materials = library._filtered(db, scope, ContentFilter(
                kind='video' if payload.contentType == 'video' else 'article', status='success'))
        recent = [record for record in materials if record.options.get('source') == 'draft-generation'
                  and datetime.fromisoformat(record.created_at).replace(tzinfo=timezone.utc).timestamp() > since][:25]
        count = len(jobs) + len(recent)
        texts: tuple[str, ...] = ()
        if count:
            samples = json.dumps({'aiLogs': [job.model_dump(mode='json', include={'request', 'response'}) for job in jobs],
                                  'materials': [record.model_dump(mode='json', include={'title', 'description', 'topics', 'options'}) for record in recent]}, ensure_ascii=False)
            result = await self.runtime.memory(MemoryRun(persona=persona, request_key='draft-memory-'+uuid.uuid4().hex,
                model=payload.plannerModel, contentType=payload.contentType, samples=samples))
            texts = tuple(dict.fromkeys(item.text for item in result.items))[:20]
        stamp = time.time()
        existing = self.list(persona, payload.contentType)
        memory = DraftMemory(id=existing[0].id if existing else uuid.uuid4().hex, userId=persona,
            contentType=payload.contentType, sampleCount=count, createdAt=existing[0].createdAt if existing else stamp,
            updatedAt=stamp, lastGeneratedAt=stamp,
            items=tuple(MemoryItem(id=uuid.uuid4().hex, text=text, createdAt=stamp, updatedAt=stamp) for text in texts))
        with self.store.base.connect() as db:
            db.execute('INSERT INTO draft_memory VALUES(?,?,?) ON CONFLICT(persona,content_type) DO UPDATE SET body=excluded.body',
                       (persona, payload.contentType, memory.model_dump_json()))
        return memory
