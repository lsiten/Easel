"""Checkpointed draft pipelines using existing native generation services."""
from __future__ import annotations
from collections.abc import Callable
from pathlib import Path
import anyio
from pydantic import ValidationError
from easel.content_library import ContentLibrary
from easel.content_library_models import ContentError, ContentScope
from easel.generation_draft_models import DraftCreated, DraftJob, DraftPlan, DraftRequest, DraftRuntime
from easel.generation_draft_store import DraftStore
from easel.generation_draft_memory import DraftMemories
from easel.generation_draft_projection import project_draft
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.publish_recovery import worker_owner
from easel.ownership import OwnershipError
from easel.remote_media import RemoteMediaError


class DraftGeneration:
    def __init__(self, outputs: Path, runtime: DraftRuntime) -> None:
        self.store = DraftStore(GenerationStore(outputs))
        self.runtime = runtime
        self.memory = DraftMemories(self.store, runtime)

    def submit(self, persona: str, payload: DraftRequest, key: str) -> DraftCreated:
        self.runtime.validate(persona, payload)
        library = ContentLibrary(self.store.base.outputs)
        scope = ContentScope(persona=persona, domain='material')
        with library.connect() as db:
            group = library.group(db, scope, payload.groupId) if payload.groupId else library.ensure_default(db, scope)
        expected = 'article' if payload.kind == 'image-text' else 'video'
        if group.kind is not None and group.kind != expected:
            raise ContentError('group_content_kind_mismatch')
        normalized = payload.model_copy(update={'groupId': group.id})
        return self.store.enqueue(persona, normalized, key)

    async def execute(self, identifier: str = '') -> DraftJob | None:
        owner = worker_owner()
        job = self.store.claim(owner, identifier)
        if job is None:
            return None
        try:
            if job.plan is None:
                if job.request.draftType == 'draft':
                    kind = 'image-text' if job.request.kind == 'image-text' else 'video'
                    memories = () if job.request.disableMemory else self.memory.list(job.persona, kind)
                    plan = await self.runtime.plan(job, tuple(item.text for memory in memories for item in memory.items))
                    if not plan.title.strip():
                        raise GenerationError('draft_plan_title_missing', 502)
                else:
                    plan = DraftPlan(videoPrompt=job.request.prompt,
                        imagePrompts=(job.request.prompt,) * job.request.imageCount if job.request.kind == 'image-text' else ())
                expected = job.request.imageCount if job.request.kind == 'image-text' else 1
                if job.request.kind == 'image-text' and len(plan.imagePrompts) != expected:
                    raise GenerationError('draft_plan_image_count_invalid', 502)
                if job.request.kind == 'video' and not plan.videoPrompt.strip():
                    raise GenerationError('draft_plan_video_prompt_missing', 502)
                job = self.store.save(job.model_copy(update={'plan': plan}), owner)
            expected = job.request.imageCount if job.request.kind == 'image-text' else 1
            while len(job.media) < expected:
                media = await self.runtime.media(job, len(job.media))
                job = self.store.save(job.model_copy(update={'media': (*job.media, media)}), owner)
            result = project_draft(self.store.base.outputs, job)
            return self.store.save(job.model_copy(update={'state': 'succeeded', 'response': result}), owner)
        except (GenerationError, ContentError) as error:
            state = 'unknown' if any(word in error.code for word in ('unknown', 'interrupted', 'timeout', 'running', 'submitting')) else 'failed'
            return self.store.save(job.model_copy(update={'state': state, 'errorMessage': error.code}), owner)
        except (OwnershipError, RemoteMediaError):
            return self.store.save(job.model_copy(update={'state': 'failed', 'errorMessage': 'draft_media_ownership_invalid'}), owner)
        except (ValidationError, OSError, TimeoutError):
            return self.store.save(job.model_copy(update={'state': 'unknown', 'errorMessage': 'draft_runtime_interrupted'}), owner)
        except anyio.get_cancelled_exc_class():
            # Safe checkpoints and stable child request keys permit reconciliation, never blind provider retries.
            self.store.save(job.model_copy(update={'state': 'queued', 'errorMessage': 'draft_worker_interrupted'}), owner)
            raise


async def _dispatch_draft_jobs(factory: Callable[[], DraftGeneration]) -> None:
    from easel.generation_dispatch import dispatch_pool
    from easel.generation_queue import QueuePolicy
    service = factory()

    async def execute() -> bool:
        return await factory().execute() is not None

    await dispatch_pool(execute, service.store.recover, QueuePolicy('draft', service.store.base.outputs))


async def dispatch_drafts(factory: Callable[[], DraftGeneration]) -> None:
    from easel.generation_draft_scheduler import dispatch_draft_memory
    async with anyio.create_task_group() as group:
        group.start_soon(dispatch_draft_memory, factory)
        await _dispatch_draft_jobs(factory)
