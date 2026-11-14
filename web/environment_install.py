from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter
from easel.network_proxy import load_proxy_config, proxy_environment

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALL_TOOL = PROJECT_ROOT / "skills" / "shared" / "scripts" / "install_tool.py"
UNINSTALL_TOOL = PROJECT_ROOT / "skills" / "shared" / "scripts" / "uninstall_tool.py"
ENV_FILE = PROJECT_ROOT / ".env"
EngineAction = Literal["list", "check", "install", "uninstall"]
_PACKAGE_JSON = TypeAdapter(dict[str, JsonValue])


class ToolStatus(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    state: Literal["ok", "missing", "no_dir", "fail"]
    version: str | None = None
    detail: str | None = None
    name: str
    group: str
    group_name: str | None = None
    desc: str
    big: bool = False
    uninstallable: bool = False


class EnvToolsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    python: str
    tools: tuple[ToolStatus, ...]
    cachedAt: int | None = None


class ToolDeclaration(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    uninstallable: bool = False


class ToolListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tools: tuple[ToolDeclaration, ...]


class InstallResult(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    state: Literal["ok", "fail"]
    version: str | None = None
    strategy: str | None = None
    detail: str | None = None


class InstallRunResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    results: tuple[InstallResult, ...]


class ProjectDirectoryError(ValueError):
    """The selected path is not a usable Remotion project directory."""


def normalize_project_dir(raw: str | None) -> str | None:
    """Parse and validate a selected Remotion project directory."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        directory = Path(value).expanduser().resolve()
    except (OSError, RuntimeError) as error:
        raise ProjectDirectoryError("目录路径无效") from error
    if not directory.is_dir():
        raise ProjectDirectoryError("所选路径不是目录")
    package_json = directory / "package.json"
    if not package_json.is_file():
        raise ProjectDirectoryError("所选目录没有 package.json，不是 Remotion 工程")
    try:
        package = _PACKAGE_JSON.validate_json(package_json.read_bytes())
    except (OSError, ValueError) as error:
        raise ProjectDirectoryError("package.json 无法读取") from error
    dependency_names: set[str] = set()
    for field in ("dependencies", "devDependencies"):
        dependencies = package.get(field)
        if isinstance(dependencies, dict):
            dependency_names.update(dependencies)
    if not {"remotion", "@remotion/cli"}.intersection(dependency_names):
        raise ProjectDirectoryError("package.json 中没有 Remotion 依赖")
    return str(directory)


def engine_command(action: EngineAction, tool_id: str | None = None,
                   project_dir: str | None = None) -> list[str]:
    """Build the engine command without interpolating user input into a shell."""
    script = UNINSTALL_TOOL if action == "uninstall" else INSTALL_TOOL
    command = [sys.executable, str(script), "--json", action]
    if tool_id:
        command.append(tool_id)
    if project_dir:
        command.extend(["--dir", project_dir])
    return command


def run_engine(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run one non-interactive installation engine command."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
        env=proxy_environment(os.environ, load_proxy_config(ENV_FILE)),
    )


def install_tool_ids() -> frozenset[str]:
    """Return the tool ids declared by the installation engine."""
    try:
        proc = run_engine(engine_command("list"), 30)
        payload = ToolListResponse.model_validate_json(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return frozenset()
    return frozenset(tool.id for tool in payload.tools)


def uninstallable_tool_ids() -> frozenset[str]:
    """Return tools that have a safe, app-managed removal recipe."""
    try:
        proc = run_engine(engine_command("list"), 30)
        payload = ToolListResponse.model_validate_json(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return frozenset()
    return frozenset(tool.id for tool in payload.tools if tool.uninstallable)


def list_directories(path: str) -> tuple[Path, tuple[Path, ...]]:
    """Return a resolved directory and its visible child directories."""
    start = Path(path).expanduser() if path.strip() else PROJECT_ROOT
    try:
        base = start.resolve()
    except (OSError, RuntimeError) as error:
        raise ProjectDirectoryError("目录路径无效") from error
    if not base.is_dir():
        raise ProjectDirectoryError("所选路径不是目录")
    try:
        children = tuple(
            child.resolve()
            for child in sorted(base.iterdir(), key=lambda item: item.name.lower())
            if child.is_dir() and not child.name.startswith(".")
        )
    except OSError as error:
        raise ProjectDirectoryError("无法读取该目录") from error
    return base, children


def parse_engine_results(raw: str) -> tuple[InstallResult, ...]:
    """Parse successful engine output for one environment job."""
    return InstallRunResponse.model_validate(json.loads(raw)).results
