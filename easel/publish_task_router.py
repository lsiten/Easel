"""Flow and task APIs sharing Easel's protected publish boundary."""
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel, StrictBool

from easel.publish_tasks import FlowRequest, TaskStore


class TaskControl(BaseModel):
    persona: str
    publish_at: datetime | None = None
    reviewed: StrictBool = False
    approval_digest: str = ''
    approval_expires_at: float | None = None


def create_router(root: Callable[[], Path], validate_profile: Callable[[str], bool],
                  valid_platform: Callable[[str], bool], require_write: Callable[[Request], None],
                  control_run: Callable[[str, str, dict], None],
                  validate_flow: Callable[[FlowRequest], None],
                  prepare: Callable[[str, dict], dict]) -> APIRouter:
    router = APIRouter(prefix='/api/publish-flows')

    def owner(persona: str) -> None:
        if not validate_profile(persona):
            raise HTTPException(400, '画像不存在或名称非法')

    def store() -> TaskStore:
        return TaskStore(root() / '_publish')

    @router.post('')
    def create_flow(payload: FlowRequest, request: Request):
        require_write(request)
        owner(payload.persona)
        if any(not valid_platform(item.platform) for item in payload.items):
            raise HTTPException(400, '包含不支持发布的平台')
        if not store().has_flow(payload.persona, payload.flow_id):
            validate_flow(payload)
        try:
            return store().create(payload, prepare)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @router.get('')
    def flows(persona: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        owner(persona)
        return store().flows(persona, limit, offset)

    @router.get('/{flow_id}')
    def flow(flow_id: str, persona: str):
        owner(persona)
        try:
            return store().flow(persona, flow_id)
        except KeyError as error:
            raise HTTPException(404, '发布流程不存在') from error

    @router.get('/tasks/{task_id}/detail')
    def task(task_id: str, persona: str):
        owner(persona)
        try:
            return store().task(persona, task_id)
        except KeyError as error:
            raise HTTPException(404, '发布任务不存在') from error

    @router.post('/tasks/{task_id}/{action}')
    def control(task_id: str, action: str, payload: TaskControl, request: Request):
        require_write(request)
        owner(payload.persona)
        if action not in ('publish-now', 'retry', 'cancel', 'reschedule'):
            raise HTTPException(404, '未知任务操作')
        stamp = payload.publish_at
        if action in ('publish-now', 'retry'):
            stamp = datetime.now(timezone.utc)
        try:
            tasks = store()
            result = tasks.control(payload.persona, task_id, action, publish_at=stamp,
                                   approval=payload.model_dump(exclude_none=True), before_change=control_run)
            if result['state'] == 'preparing':
                from easel.publish_task_preparation import prepare_one
                prepare_one(tasks, prepare, task_id)
                result = tasks.task(payload.persona, task_id)
            return result
        except KeyError as error:
            raise HTTPException(404, '发布任务不存在') from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return router
