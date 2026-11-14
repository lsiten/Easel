"""Background AI organization with persona-scoped progress and validated writes."""
from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Literal, Protocol, TypedDict

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from easel.asset_plan import AssetFile, AssetPlanError, organization_prompt, parse_plan


class OrganizeResult(TypedDict):
    ok: bool
    placed: int
    skipped: list[str]


class AssetLibrary(Protocol):
    def scan(self, profile: str, *, changed_only: bool = False) -> list[AssetFile]: ...
    def organize(self, profile: str, *, dry_run: bool, plan: list[dict]) -> OrganizeResult: ...


class AgentRunner(Protocol):
    def __call__(self, prompt: str, profile: str, session: str) -> str: ...


class OrganizationJob(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    profile: str
    status: Literal["running", "done", "failed"] = "running"
    total: int
    completed: int = 0
    placed: int = 0
    dry_run: bool = False
    error: str = ""
    updated: float
    owner_pid: int = 0


class OrganizationRequest(BaseModel):
    dry_run: bool = False


class AssetOrganizer:
    """Own one in-flight job per persona; disk snapshots survive page refreshes."""

    def __init__(self, directory: Path, library: Callable[[], AssetLibrary], runner: AgentRunner) -> None:
        self.directory = directory
        self.library = library
        self.runner = runner
        self.lock = threading.RLock()
        self.active: set[str] = set()

    def _path(self, profile: str) -> Path:
        name = hashlib.sha256(profile.encode()).hexdigest()[:24]
        return self.directory / f"{name}.json"

    def _save(self, job: OrganizationJob) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(job.profile)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(job.model_dump_json(), encoding="utf-8")
        temporary.replace(path)

    def current(self, profile: str) -> OrganizationJob | None:
        with self.lock:
            try:
                job = OrganizationJob.model_validate_json(self._path(profile).read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (ValidationError, OSError) as error:
                raise HTTPException(500, "素材整理进度无法读取，请重试") from error
            owner_alive = job.id in self.active
            if job.owner_pid and job.owner_pid != os.getpid():
                try:
                    os.kill(job.owner_pid, 0)
                    owner_alive = True
                except ProcessLookupError:
                    owner_alive = False
                except PermissionError:
                    owner_alive = True
            if job.status == "running" and not owner_alive:
                job = job.model_copy(update={"status": "failed", "error": "服务已重启，整理中断，请重试", "updated": time.time()})
                self._save(job)
            return job

    def start(self, profile: str, dry_run: bool) -> tuple[OrganizationJob, list[AssetFile]]:
        with self.lock:
            previous = self.current(profile)
            if previous and previous.status == "running":
                return previous, []
            files = self.library().scan(profile, changed_only=True)
            job = OrganizationJob(id=uuid.uuid4().hex, profile=profile, total=len(files),
                                  dry_run=dry_run, status="running" if files else "done", updated=time.time(), owner_pid=os.getpid())
            if files:
                self.active.add(job.id)
            self._save(job)
            return job, files

    def run(self, job: OrganizationJob, files: list[AssetFile]) -> None:
        try:
            library = self.library()
            for offset in range(0, len(files), 12):
                batch = files[offset:offset + 12]
                response = self.runner(organization_prompt(job.profile, batch), job.profile, f"asset-{job.id}-{offset}")
                plan = parse_plan(response, batch)
                for index, item in enumerate(plan.items):
                    result = library.organize(job.profile, dry_run=job.dry_run, plan=[item.model_dump()])
                    if not result["ok"] or result["skipped"]:
                        raise AssetPlanError("部分素材无法整理，请检查源文件后重试")
                    job = job.model_copy(update={"completed": offset + index + 1, "placed": job.placed + result["placed"],
                                                 "updated": time.time()})
                    with self.lock:
                        self._save(job)
            job = job.model_copy(update={"status": "done", "updated": time.time()})
        except (AssetPlanError, ValidationError) as error:
            message = error.reason if isinstance(error, AssetPlanError) else "AI 未返回有效的素材整理方案，请重试"
            job = job.model_copy(update={"status": "failed", "error": message, "updated": time.time()})
        except HTTPException as error:
            job = job.model_copy(update={"status": "failed", "error": str(error.detail), "updated": time.time()})
        except Exception:  # noqa: BROAD_EXCEPT_OK — background task boundary, report failure to its caller.
            job = job.model_copy(update={"status": "failed", "error": "Agent 整理失败，请检查运行时、模型连接或工具权限后重试",
                                         "updated": time.time()})
        finally:
            with self.lock:
                self.active.discard(job.id)
                self._save(job)


def organization_router(organizer: AssetOrganizer, validate_profile: Callable[[str], bool],
                        require_agent: Callable[[], None]) -> APIRouter:
    router = APIRouter()

    def profile_name(persona: str | None) -> str:
        if not persona or not validate_profile(persona):
            raise HTTPException(400, "请先选择有效的账号画像，再整理素材")
        return persona

    @router.post("/api/assets/organize", status_code=202)
    def start(req: OrganizationRequest, background: BackgroundTasks, persona: str | None = None):
        profile = profile_name(persona)
        require_agent()
        job, files = organizer.start(profile, req.dry_run)
        if files:
            background.add_task(organizer.run, job, files)
        return job

    @router.get("/api/assets/organize")
    def current(persona: str | None = None):
        return organizer.current(profile_name(persona))

    @router.get("/api/assets/organize/{identifier}")
    def progress(identifier: str, persona: str | None = None):
        job = organizer.current(profile_name(persona))
        if not job or job.id != identifier:
            raise HTTPException(404, "未找到当前画像的素材整理任务")
        return job

    return router
