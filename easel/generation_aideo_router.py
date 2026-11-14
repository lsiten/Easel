"""The three source Aideo HTTP operations, scoped to Easel personas."""
from collections.abc import Callable
from fastapi import APIRouter, HTTPException, Query, Request
from easel.generation_aideo_models import AideoRequest, StyleTransferRequest, DramaRecapRequest
from easel.generation_aideo_workflows import AideoWorkflows, public_workflow
from easel.generation_aideo_service import AideoGeneration, public_job
from easel.generation_router import generation_errors


def create_router(factory: Callable[[], AideoGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai/aideo', tags=['aideo'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    @router.post('/tasks')
    async def submit(payload: AideoRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            job = await factory().submit(payload)
            return {'taskId': job.id}

    @router.get('/tasks')
    async def listing(request: Request, persona: str, page: int = Query(1, ge=1), pageSize: int = Query(30, ge=1, le=200)):
        owner(persona, request)
        with generation_errors():
            jobs, total = factory().store.page(persona, (page, pageSize))
            return {'list': [public_job(job) for job in jobs], 'total': total, 'page': page, 'pageSize': pageSize}

    @router.get('/tasks/{task_id}')
    async def detail(task_id: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            return public_job(await factory().get(persona, task_id))

    @router.post('/style-transfer/tasks')
    async def style_submit(payload: StyleTransferRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            return public_workflow(await AideoWorkflows(factory()).submit(payload))

    @router.post('/drama-recap/tasks')
    async def recap_submit(payload: DramaRecapRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            return public_workflow(await AideoWorkflows(factory()).submit(payload))

    @router.get('/style-transfer/tasks/{task_id}')
    async def style_detail(task_id: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            service = factory()
            if service.store.get(persona, task_id).request_model != 'video-style-transfer':
                raise HTTPException(404, 'aideo_workflow_not_found')
            return public_workflow(await AideoWorkflows(service).get(persona, task_id))

    @router.get('/drama-recap/tasks/{task_id}')
    async def recap_detail(task_id: str, persona: str, request: Request):
        owner(persona, request)
        with generation_errors():
            service = factory()
            if service.store.get(persona, task_id).request_model != 'drama-recap':
                raise HTTPException(404, 'aideo_workflow_not_found')
            return public_workflow(await AideoWorkflows(service).get(persona, task_id))

    return router
