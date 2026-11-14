"""Admin-only setup plans and secret-safe application configuration."""
from collections.abc import Callable, Mapping
from pathlib import Path
import re
import time
import hashlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import Response
from collections.abc import Awaitable
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from easel.channel_application import ApplicationSetupRequest, ApplicationUpdate, application_updates, application_view, setup_prompt
from easel.channel_publish_router import _errors
from easel.channels import CredentialStore, definition
from easel.harnesses.store import read_json, write_json
from easel.harnesses.base import HarnessError


class SecretSafeRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()
        async def safe(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                raise HTTPException(422, '应用配置参数无效，请检查字段类型') from error
        return safe


class SetupProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    persona: str
    state: Literal['running', 'waiting_user', 'complete']
    message: str = Field(default='', max_length=500)
    task_space_id: int | None = Field(default=None, gt=0)


def create_router(root: Callable[[], Path], settings: Callable[[], Mapping[str, str]], env_file: Callable[[], Path],
                  profile: Callable[[str], bool], require_write: Callable[[Request], None],
                  save: Callable[[dict[str, str]], None], agent_ready: Callable[[], bool]) -> APIRouter:
    router = APIRouter(prefix='/api/channel-applications', tags=['channel-applications'], route_class=SecretSafeRoute)

    def owner(persona: str, request: Request) -> None:
        require_write(request)
        if not profile(persona):
            raise HTTPException(400, '画像不存在或名称非法')

    def setup_path(identifier: str) -> Path:
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            raise HTTPException(404, '配置任务不存在')
        return root() / '_channel-setup' / f'{identifier}.json'

    @router.get('/{platform}')
    def status(platform: str, persona: str, request: Request):
        owner(persona, request)
        with _errors():
            result = application_view(platform, settings())
            connected = bool(CredentialStore(root() / '_channels').accounts(persona, definition(platform).platform))
            return result.model_copy(update={'connected': connected})

    @router.post('/{platform}')
    def update(platform: str, payload: ApplicationUpdate, request: Request):
        owner(payload.persona, request)
        with _errors():
            save(application_updates(platform, payload, settings()))
            return status(platform, payload.persona, request)

    @router.post('/{platform}/setup')
    def prepare(platform: str, payload: ApplicationSetupRequest, request: Request):
        owner(payload.persona, request)
        with _errors():
            definition(platform)
            if not agent_ready():
                raise HTTPException(409, '请在设置中选择已安装的工具型 Agent，以执行 Ego 应用配置')
            identifier = hashlib.sha256(f'{payload.persona}\0{platform}\0{payload.session_id}'.encode()).hexdigest()[:32]
            previous = read_json(setup_path(identifier))
            data = {'setup_id': identifier, 'platform': platform, 'persona': payload.persona,
                'session_id': payload.session_id, 'state': 'created', 'message': '', 'created_at': time.time()}
            if previous:
                data = previous
            else:
                write_json(setup_path(identifier), data)
            return {**data, 'prompt': setup_prompt(platform, payload.persona, identifier, str(request.base_url), env_file(), settings())}

    @router.get('/{platform}/setup/{identifier}')
    def progress(platform: str, identifier: str, persona: str, request: Request):
        owner(persona, request)
        try:
            data = read_json(setup_path(identifier))
        except HarnessError as error:
            raise HTTPException(409, '配置任务记录损坏，请重新发起配置') from error
        if data.get('persona') != persona or data.get('platform') != platform:
            raise HTTPException(404, '配置任务不存在')
        return data

    @router.post('/{platform}/setup/{identifier}')
    def advance(platform: str, identifier: str, payload: SetupProgress, request: Request):
        data = progress(platform, identifier, payload.persona, request)
        result = status(platform, payload.persona, request)
        state = payload.state
        if state == 'complete':
            state = 'connected' if result.connected else 'configured' if result.configured else 'waiting_user'
        data.update(state=state, message=payload.message, updated_at=time.time(), task_space_id=payload.task_space_id)
        write_json(setup_path(identifier), data)
        return {**data, 'application': result}

    return router
