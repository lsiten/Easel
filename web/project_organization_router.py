"""HTTP surface for physical project organization and rollback."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict

from easel.project_organization import ProjectOrganizer
from easel.project_organization_plan import ProjectOrganizationError


class OrganizeProjectRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project: str = ""


def create_project_organization_router(
    organizer: ProjectOrganizer,
    valid_profile: Callable[[str], bool],
    require_agent: Callable[[], None],
) -> APIRouter:
    router = APIRouter(tags=["content"])

    def profile_name(persona: str | None) -> str:
        if not persona or not valid_profile(persona):
            raise HTTPException(400, "请先选择有效的账号画像")
        return persona

    @router.post("/api/projects/organize", status_code=202)
    def organize(payload: OrganizeProjectRequest, background: BackgroundTasks,
                 persona: str | None = None):
        profile = profile_name(persona)
        require_agent()
        try:
            job = organizer.start(profile, payload.project.strip())
        except ProjectOrganizationError as error:
            raise HTTPException(error.status, error.code) from error
        if job.total:
            background.add_task(organizer.run, job)
        return job

    @router.get("/api/projects/organize")
    def current(persona: str | None = None):
        try:
            return organizer.current(profile_name(persona))
        except ProjectOrganizationError as error:
            raise HTTPException(error.status, error.code) from error

    @router.post("/api/projects/organize/{identifier}/rollback")
    def rollback(identifier: str, persona: str | None = None):
        try:
            return organizer.rollback(profile_name(persona), identifier)
        except ProjectOrganizationError as error:
            raise HTTPException(error.status, error.code) from error

    return router
