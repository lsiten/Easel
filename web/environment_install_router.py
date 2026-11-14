from __future__ import annotations

import subprocess
import threading
import time
import uuid
import os
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from web.environment_install import (
    EngineAction,
    ENV_FILE,
    EnvToolsResponse,
    InstallResult,
    ProjectDirectoryError,
    engine_command,
    install_tool_ids,
    list_directories,
    normalize_project_dir,
    parse_engine_results,
    run_engine,
    uninstallable_tool_ids,
)
from easel.network_proxy import load_proxy_config, proxy_environment

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_SECONDS = 15

router = APIRouter()


class EnvInstallRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    dir: str | None = None


class DirectoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    path: str


class DirectoryListing(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    name: str
    parent: str
    dirs: tuple[DirectoryEntry, ...]
    quick: tuple[DirectoryEntry, ...]


class EnvironmentJob(BaseModel):
    """Mutable background job state updated by its worker thread."""

    model_config = ConfigDict(validate_assignment=True)

    jobId: str
    id: str
    state: Literal["running", "ok", "fail"]
    lines: list[str]
    result: InstallResult | None = None
    started: int
    ended: int | None = None


_env_tools_cache: dict[str, tuple[float, EnvToolsResponse]] = {}
_env_jobs: dict[str, EnvironmentJob] = {}


def _project_dir(raw: str | None) -> str | None:
    try:
        return normalize_project_dir(raw)
    except ProjectDirectoryError as error:
        raise HTTPException(400, str(error)) from error


@router.get("/api/env/tools", response_model=EnvToolsResponse)
async def api_env_tools(refresh: bool = False,
                        project_dir: str | None = Query(None, alias="dir")) -> EnvToolsResponse:
    """Run the installation engine health check, optionally against a project directory."""
    directory = _project_dir(project_dir)
    cache_key = directory or ""
    cached_at, cached = _env_tools_cache.get(cache_key, (0.0, EnvToolsResponse(
        python="", tools=(), cachedAt=None)))
    if not refresh and cached.python and time.time() - cached_at < CACHE_SECONDS:
        return cached
    try:
        proc = await anyio.to_thread.run_sync(
            run_engine, engine_command("check", project_dir=directory), 240)
    except subprocess.TimeoutExpired as error:
        raise HTTPException(504, "环境体检超时，请稍后再试") from error
    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or "").strip()[-300:] or "无输出"
        raise HTTPException(500, f"环境体检失败：{detail}")
    try:
        payload = EnvToolsResponse.model_validate_json(proc.stdout)
    except ValueError as error:
        raise HTTPException(500, f"体检输出解析失败：{error}") from error
    payload = payload.model_copy(update={"cachedAt": int(time.time())})
    _env_tools_cache[cache_key] = (time.time(), payload)
    return payload


@router.get("/api/env/pick-dir", response_model=DirectoryListing)
async def api_env_pick_dir(path: str = "") -> DirectoryListing:
    """List local directories so the Web UI can choose a project folder."""
    try:
        base, children = await anyio.to_thread.run_sync(list_directories, path)
    except ProjectDirectoryError as error:
        raise HTTPException(400, str(error)) from error
    home = Path.home().resolve()
    quick = tuple(
        DirectoryEntry(name=name, path=str(directory))
        for name, directory in (
            ("主目录", home),
            ("项目", PROJECT_ROOT),
            ("下载", home / "Downloads"),
            ("文档", home / "Documents"),
        )
        if directory.is_dir()
    )
    return DirectoryListing(
        path=str(base),
        name=base.name or str(base),
        parent=str(base.parent),
        dirs=tuple(DirectoryEntry(name=child.name, path=str(child)) for child in children),
        quick=quick,
    )


@router.post("/api/env/install")
async def api_env_install(req: EnvInstallRequest) -> dict[str, str]:
    """Start an installation job and return its polling handle."""
    return await _start_environment_job(req, "install")


@router.post("/api/env/uninstall")
async def api_env_uninstall(req: EnvInstallRequest) -> dict[str, str]:
    """Start an uninstall job for a removable component."""
    removable = await anyio.to_thread.run_sync(uninstallable_tool_ids)
    if req.id.strip() not in removable:
        raise HTTPException(400, "该组件是系统或外部组件，不支持由 Easel 卸载")
    return await _start_environment_job(req, "uninstall")


async def _start_environment_job(req: EnvInstallRequest,
                                 action: EngineAction) -> dict[str, str]:
    """Validate one tool request and start its background job."""
    tool_id = req.id.strip()
    known = await anyio.to_thread.run_sync(install_tool_ids)
    if tool_id not in known:
        raise HTTPException(400, f"无效的工具 id：{tool_id[:40]!r}")
    directory = _project_dir(req.dir)
    job_id = uuid.uuid4().hex[:16]
    job = EnvironmentJob(
        jobId=job_id,
        id=tool_id,
        state="running",
        lines=[],
        started=int(time.time()),
    )
    _env_jobs[job_id] = job
    threading.Thread(target=_run_job, args=(job, directory, action), daemon=True).start()
    return {"jobId": job_id, "id": tool_id, "state": "running"}


def _run_job(job: EnvironmentJob, directory: str | None,
             action: EngineAction) -> None:
    try:
        proc = subprocess.Popen(
            engine_command(action, tool_id=job.id, project_dir=directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            env=proxy_environment(os.environ, load_proxy_config(ENV_FILE)),
        )
        out: list[str] = []
        reader = threading.Thread(
            target=lambda: out.append(proc.stdout.read() if proc.stdout else ""),
            daemon=True,
        )
        reader.start()
        if proc.stderr is not None:
            for line in proc.stderr:
                if line.strip():
                    job.lines = (job.lines + [line.rstrip()])[-40:]
        try:
            proc.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)
            raise
        reader.join(timeout=30)
        result = parse_engine_results("".join(out))[0]
        job.result = result
        job.state = "ok" if result.state == "ok" else "fail"
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as error:
        job.state = "fail"
        job.result = InstallResult(
            id=job.id,
            state="fail",
            detail=f"{type(error).__name__}: {error}",
        )
    finally:
        job.ended = int(time.time())
        _env_tools_cache.clear()


@router.get("/api/env/job/{job_id}", response_model=EnvironmentJob)
async def api_env_job(job_id: str) -> EnvironmentJob:
    """Return progress for one installation job."""
    job = _env_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在（服务可能重启过）")
    return job
