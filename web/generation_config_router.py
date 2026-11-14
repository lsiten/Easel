"""Admin-only configuration workflow; changes apply to subsequent requests."""
from fastapi import APIRouter, Request, HTTPException, Depends
from easel.generation_models import GenerationError
from easel.channel_application_router import SecretSafeRoute
from easel.generation_config_models import ConfigUpdate
from easel.generation_app_config import public_config, save_config
from web import app as server


def require_config_admin(request: Request) -> None:
    """Persona-scoped credentials cannot grant application-wide administration."""
    if getattr(request.state, 'caller_key', None) is not None:
        raise HTTPException(403, 'configuration_requires_global_admin')
    server._require_side_effect_access(request)


def create_router() -> APIRouter:
    router = APIRouter(route_class=SecretSafeRoute, tags=['settings'], dependencies=[Depends(require_config_admin)])

    @router.get('/api/config')
    @router.get('/api/ai/config')
    def read(request: Request):
        return public_config(server.OUTPUTS_DIR, server.read_settings(server.ENV_FILE))

    @router.post('/api/config/validate')
    @router.post('/api/ai/config/validate')
    def validate(payload: ConfigUpdate, request: Request):
        return {'valid': True}

    @router.put('/api/config')
    @router.put('/api/ai/config')
    def save(payload: ConfigUpdate, request: Request):
        try:
            save_config(server.OUTPUTS_DIR, payload, server.read_settings(server.ENV_FILE))
        except GenerationError as error:
            raise HTTPException(error.status, error.code) from error
        return public_config(server.OUTPUTS_DIR, server.read_settings(server.ENV_FILE))

    @router.post('/api/config/restart')
    @router.post('/api/ai/config/restart')
    def apply(request: Request):
        return {'applied': True, 'mode': 'per-request', 'revision': public_config(server.OUTPUTS_DIR, server.read_settings(server.ENV_FILE))['revision']}

    return router
