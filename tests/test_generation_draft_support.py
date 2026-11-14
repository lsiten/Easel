"""External runtime fixture for real draft routes/database/material projection."""
from pathlib import Path
import hashlib
import json
from fastapi import FastAPI, HTTPException, Request
import httpx
from PIL import Image
from easel.generation_draft import DraftGeneration
from easel.generation_draft_models import (DraftJob, DraftMedia, DraftPlan, DraftRequest, DraftPricing,
    MemoryResult, MemoryRun, MemoryText)
from easel.generation_draft_router import create_draft_router, create_internal_draft_router
from easel.generation_models import GeneratedAsset, GenerationError


class Runtime:
    def __init__(self, outputs: Path) -> None:
        self.outputs = outputs
        self.fail: set[str] = set()
        self.calls: list[tuple[str, int]] = []
        self.memories: list[MemoryRun] = []
        self.plan_memories: list[tuple[str, ...]] = []

    def validate(self, persona: str, payload: DraftRequest) -> None:
        if payload.kind == 'video-url' and str(payload.videoUrl).startswith('http://127.'):
            raise GenerationError('draft_remote_url_rejected')

    async def plan(self, job: DraftJob, memory: tuple[str, ...]) -> DraftPlan:
        self.plan_memories.append(memory)
        return DraftPlan(title='Morning pottery workshop', description='Learn to shape a clay cup at the studio.', topics=('pottery', 'workshop'),
            videoPrompt='Hands shape a clay cup on a wheel.',
            imagePrompts=tuple('Clay cup composition '+str(i) for i in range(job.request.imageCount)) if job.request.kind == 'image-text' else ())

    async def media(self, job: DraftJob, position: int) -> DraftMedia:
        self.calls.append((job.id, position))
        if job.id in self.fail and position == 1:
            raise GenerationError('draft_test_provider_failed', 502)
        folder = self.outputs / ('draft-'+job.id)
        folder.mkdir(exist_ok=True)
        (folder/'.easel.json').write_text(json.dumps({'profile': job.persona, 'source': 'draft-generation'}))
        kind = 'image' if job.request.kind == 'image-text' else 'video'
        file = folder / (str(position)+('.png' if kind == 'image' else '.mp4'))
        if kind == 'image':
            Image.new('RGB', (24, 24), 'blue').save(file)
        else:
            file.write_bytes(b'external-video-runtime-result')
        content = file.read_bytes()
        return DraftMedia(asset=GeneratedAsset(path=file.relative_to(self.outputs).as_posix(), kind=kind,
            size=len(content), sha256=hashlib.sha256(content).hexdigest()), generationId='child-'+job.id+'-'+str(position))

    async def memory(self, run: MemoryRun) -> MemoryResult:
        self.memories.append(run)
        return MemoryResult(items=(MemoryText(text='Prefer short workshop captions.'), MemoryText(text='Prefer short workshop captions.')))

    def pricing(self, persona: str) -> DraftPricing:
        return DraftPricing(imageModels=(), videoModels=())


def setup(outputs: Path) -> tuple[DraftGeneration, Runtime, httpx.AsyncClient]:
    runtime = Runtime(outputs)
    service = DraftGeneration(outputs, runtime)
    def authorize(request: Request, persona: str) -> str:
        owner = request.headers.get('X-Owner', 'alice')
        if persona and persona != owner:
            raise HTTPException(403, 'owner_mismatch')
        return owner
    app = FastAPI()
    app.include_router(create_draft_router(lambda: service, authorize))
    app.include_router(create_internal_draft_router(lambda: service, authorize))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', timeout=10)
    return service, runtime, client
