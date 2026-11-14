from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import tomllib
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts"))

import install_tool
import uninstall_tool

from web import environment_install_router


class ToolCheckResult(TypedDict):
    id: str
    state: str
    version: str | None
    detail: str | None


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(environment_install_router.router)
    return TestClient(app)


def _remotion_project(tmp_path: Path) -> Path:
    project = tmp_path / "remotion-project"
    (project / "node_modules").mkdir(parents=True)
    (project / "package.json").write_text(
        json.dumps({
            "dependencies": {
                "remotion": "^4.0.0",
                "@remotion/cli": "^4.0.0",
            },
        }),
        encoding="utf-8",
    )
    return project


def test_env_check_uses_project_virtualenv(tmp_path: Path) -> None:
    environment_install_router._env_tools_cache.clear()

    response = _client().get("/api/env/tools?refresh=1")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert Path(payload["python"]).resolve() == Path(sys.executable).resolve()
    python = next(tool for tool in payload["tools"] if tool["id"] == "python")
    assert python["state"] == "ok", python


def test_env_check_accepts_remotion_project_directory(tmp_path: Path) -> None:
    project = _remotion_project(tmp_path)
    environment_install_router._env_tools_cache.clear()

    response = _client().get("/api/env/tools", params={"refresh": "1", "dir": str(project)})

    assert response.status_code == 200, response.text
    tools = {tool["id"]: tool for tool in response.json()["tools"]}
    assert tools["rmdeps"]["state"] == "ok"
    assert tools["rmdeps"]["uninstallable"] is True
    assert tools["node"]["uninstallable"] is False


def test_env_install_rejects_non_remotion_directory(tmp_path: Path) -> None:
    project = tmp_path / "ordinary-project"
    project.mkdir()
    (project / "package.json").write_text('{"dependencies": {"react": "19"}}', encoding="utf-8")

    response = _client().post("/api/env/install", json={"id": "rmdeps", "dir": str(project)})

    assert response.status_code == 400
    assert "Remotion" in response.json()["detail"]


def test_env_uninstall_rejects_external_component() -> None:
    response = _client().post("/api/env/uninstall", json={"id": "node"})

    assert response.status_code == 400
    assert "不支持" in response.json()["detail"]


def test_env_directory_picker_lists_child_folders(tmp_path: Path) -> None:
    child = tmp_path / "video-project"
    child.mkdir()

    response = _client().get("/api/env/pick-dir", params={"path": str(tmp_path)})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["path"] == str(tmp_path.resolve())
    assert {entry["name"] for entry in payload["dirs"]} == {"video-project"}


def test_uninstaller_removes_only_remotion_node_modules(tmp_path: Path) -> None:
    project = _remotion_project(tmp_path)
    script = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts" / "uninstall_tool.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--json", "uninstall", "rmdeps", "--dir", str(project)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert not (project / "node_modules").exists()
    assert (project / "package.json").is_file()


def test_uninstaller_removes_managed_whisper_model(tmp_path: Path) -> None:
    home = tmp_path / "home"
    model = home / "models" / "whisper-large-v3"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"model")
    script = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts" / "uninstall_tool.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--json", "uninstall", "model"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "HOME": str(home)},
    )

    assert completed.returncode == 0, completed.stderr
    assert not model.exists()


def test_environment_model_check_requires_runtime_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    model = home / "models" / "whisper-large-v3"
    model.mkdir(parents=True)
    (model / "model.bin").write_bytes(b"partial")
    script = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts" / "install_tool.py"
    environment = {**os.environ, "HOME": str(home)}

    partial = subprocess.run(
        [sys.executable, str(script), "--json", "check", "model"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert partial.returncode == 0, partial.stderr
    assert json.loads(partial.stdout)["tools"][0]["state"] == "missing"

    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    complete = subprocess.run(
        [sys.executable, str(script), "--json", "check", "model"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert complete.returncode == 0, complete.stderr
    assert json.loads(complete.stdout)["tools"][0]["state"] == "ok"


def test_biliup_install_produces_the_official_native_cli(monkeypatch) -> None:
    """安装成功后状态必须是 CLI 可执行，而不是另一个容易误装的 Python 包。"""
    tool = next(tool for tool in install_tool.TOOLS if tool.id == "biliup")
    installed = False

    def fake_find_cli(name: str, python: str) -> str | None:
        del python
        return "/managed/bin/biliup" if installed and name == "biliup" else None

    def fake_run(argv: list[str], *, cwd: str | None = None, timeout: int = 600):
        del cwd, timeout
        nonlocal installed
        if argv == ["npm", "install", "-g", "@biliup/cli@latest"]:
            installed = True
            return subprocess.CompletedProcess(argv, 0, "installed", "")
        if argv == ["/managed/bin/biliup", "--version"] and installed:
            return subprocess.CompletedProcess(argv, 0, "biliup-cli 1.3.4\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "not available")

    monkeypatch.setattr(install_tool, "_find_cli", fake_find_cli)
    monkeypatch.setattr(install_tool, "_run", fake_run)

    result = install_tool.install_tool(tool, sys.executable)

    assert result["state"] == "ok"
    assert result["version"] == "biliup-cli 1.3.4"
    assert installed is True


def test_biliup_uninstall_removes_native_and_legacy_cli_installs(monkeypatch) -> None:
    """卸载要同时清理新原生 CLI 和旧版 Python 包，避免留下不可用入口。"""
    tool = next(tool for tool in install_tool.TOOLS if tool.id == "biliup")
    native_installed = True
    legacy_installed = True

    def fake_check(tool: install_tool.Tool, python: str,
                   dir_: str | None = None) -> ToolCheckResult:
        del python, dir_
        return {
            "id": tool.id,
            "state": "ok" if native_installed else "missing",
            "version": "biliup-cli 1.3.4" if native_installed else None,
            "detail": None,
        }

    def fake_run(argv: list[str], *, cwd: str | None = None, timeout: int = 600):
        del cwd, timeout
        nonlocal native_installed, legacy_installed
        if argv == ["npm", "uninstall", "-g", "@biliup/cli"]:
            native_installed = False
            return subprocess.CompletedProcess(argv, 0, "removed", "")
        if len(argv) >= 5 and argv[1:4] == ["-m", "pip", "uninstall"] and argv[-1] == "biliup":
            legacy_installed = False
            return subprocess.CompletedProcess(argv, 0, "removed", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected command")

    monkeypatch.setattr(install_tool, "check_tool", fake_check)
    monkeypatch.setattr(install_tool, "_run", fake_run)

    result = uninstall_tool.uninstall_tool(tool, sys.executable)

    assert result.state == "ok"
    assert native_installed is False
    assert legacy_installed is False


def test_project_dependencies_enable_socks_proxy_downloads() -> None:
    """用 SOCKS 代理下载模型时，httpx 必须带 socks extra 才能建立连接。"""
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]

    assert any(dependency.startswith("httpx[socks]") for dependency in dependencies)
