"""Persona-owned AI chat endpoints independent of agent harness sessions."""
from collections.abc import Callable
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from easel.generation_chat_config import chat_models
from easel.generation_chat_models import ChatRequest, ClaudeRequest, CompletionRequest
from easel.generation_chat_service import ChatGeneration
from easel.generation_router import generation_errors


def create_router(factory: Callable[[], ChatGeneration], profile_valid: Callable[[str], bool],
                  require_access: Callable[[Request], None]) -> APIRouter:
    router = APIRouter(prefix='/api/ai', tags=['chat'])

    def owner(persona: str, request: Request) -> None:
        if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
            require_access(request)
        if not profile_valid(persona):
            raise HTTPException(400, 'persona_invalid')

    @router.get('/models/chat')
    def models(persona: str, request: Request, channel: str = '', scene: str = ''):
        owner(persona, request)
        with generation_errors():
            service = factory()
            return [item for item in chat_models(service.store.base.outputs, persona, service.settings)
                    if (not channel or channel == item.channel) and (not scene or scene in item.scenes)]

    @router.post('/chat')
    async def completion(payload: CompletionRequest, request: Request):
        owner(payload.persona, request)
        with generation_errors():
            record, response = await factory().completion(payload, origin=str(request.base_url))
            return JSONResponse(response, headers={'X-Easel-Job-Id': record.id})

    async def stream_response(payload: ChatRequest, request: Request, claude: bool) -> StreamingResponse:
        owner(payload.persona, request)
        try:
            after = int(request.headers.get('last-event-id', '0'))
            if after < 0:
                raise ValueError
        except ValueError:
            raise HTTPException(400, 'chat_resume_invalid') from None
        with generation_errors():
            record, events = await factory().stream(payload, claude, after, origin=str(request.base_url))
            return StreamingResponse(events, media_type='text/event-stream',
                                     headers={'X-Easel-Job-Id': record.id, 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @router.post('/chat/stream')
    async def stream(payload: ChatRequest, request: Request):
        return await stream_response(payload, request, False)

    @router.post('/chat/claude')
    async def claude(payload: ClaudeRequest, request: Request):
        if any(message.role not in ('user', 'assistant') for message in payload.messages):
            raise HTTPException(422, 'chat_claude_role_invalid')
        return await stream_response(payload, request, True)

    return router
