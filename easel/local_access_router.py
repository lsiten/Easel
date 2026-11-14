"""Local administration and delegated caller-key boundary."""
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from easel.local_access import AccessStore


class KeyRequest(BaseModel):
    persona: str
    name: str = Field(min_length=1, max_length=200)
    permission: str = 'read'


class LinkRequest(BaseModel):
    persona: str
    url: str
    expires_at: float | None = None


_KEY_ROUTES = ('/api/channels/', '/api/channel-publish/', '/api/publish-runs',
               '/api/analytics/', '/api/short-links', '/api/content/', '/api/local-user',
               '/api/asset-uploads/', '/api/publish-flows', '/api/publish-records', '/api/ai/', '/api/internal/ai/draft-generation')


async def validate_caller_key(request: Request, root: Path) -> bool:
    """Bind a delegated key to an explicit persona before invoking any route."""
    token = request.headers.get('x-easel-api-key', '')
    bearer = request.headers.get('authorization', '')
    if not token and bearer.startswith('Bearer eak_'):
        token = bearer[7:]
    if not token:
        return False
    identity = AccessStore(root / '_access').authenticate(token)
    if identity is None:
        raise HTTPException(401, 'API Key 无效或已撤销')
    if not request.url.path.startswith(_KEY_ROUTES):
        raise HTTPException(403, 'API Key 无权访问此接口')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and identity['permission'] != 'write':
        raise HTTPException(403, 'API Key 仅有读取权限')
    persona = request.query_params.get('persona', '')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('content-type', '').split(';')[0] == 'application/json':
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            body_persona = payload.get('persona', '')
            if request.url.path.startswith('/api/internal/ai/draft-generation'):
                body_persona = body_persona or payload.get('userId', '')
            if persona and body_persona and persona != body_persona:
                raise HTTPException(403, '画像作用域冲突')
            persona = body_persona or persona
    if persona != identity['persona']:
        raise HTTPException(403, 'API Key 不属于当前画像')
    request.state.caller_key = identity
    return True


def create_router(root: Callable[[], Path], valid_profile: Callable[[str], bool],
                  require_write: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    def store() -> AccessStore:
        return AccessStore(root() / '_access')

    def owner(persona: str) -> str:
        if not valid_profile(persona):
            raise HTTPException(400, '画像不存在或名称非法')
        return persona

    @router.post('/api/access-keys')
    def create_key(payload: KeyRequest, request: Request):
        require_write(request)
        try:
            return store().create_key(owner(payload.persona), payload.name, payload.permission)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    @router.get('/api/access-keys')
    def keys(persona: str, request: Request):
        require_write(request)
        return store().keys(owner(persona))

    @router.delete('/api/access-keys/{key_id}')
    def delete_key(key_id: str, persona: str, request: Request):
        require_write(request)
        if not store().revoke(owner(persona), key_id):
            raise HTTPException(404, 'API Key 不存在')
        return {'deleted': True}

    @router.post('/api/short-links')
    def create_link(payload: LinkRequest, request: Request):
        require_write(request)
        try:
            code = store().create_link(owner(payload.persona), payload.url, payload.expires_at)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {'code': code, 'path': f'/s/{code}'}

    @router.get('/api/short-links')
    def links(persona: str):
        return store().links(owner(persona))

    @router.delete('/api/short-links/{code}')
    def delete_link(code: str, persona: str, request: Request):
        require_write(request)
        if not store().delete_link(owner(persona), code):
            raise HTTPException(404, '短链不存在')
        return {'deleted': True}

    @router.get('/s/{code}')
    def redirect(code: str):
        url = store().resolve(code)
        if url is None:
            raise HTTPException(404, '短链不存在或已到期')
        return RedirectResponse(url, status_code=302)

    return router
