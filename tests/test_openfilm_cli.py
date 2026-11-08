"""Session routing and frame receipts through the shipped CLI entry point."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/shared/scripts/openfilm.py"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EASEL_ROOT", str(tmp_path))
    monkeypatch.setenv("EASEL_SESSION_KEY", "chat-a")
    for name in ("own film", "other"):
        project = tmp_path / "outputs" / name
        project.mkdir(parents=True)
        (project / "film.html").write_text(name)
    sessions = tmp_path / "outputs/_sessions"
    sessions.mkdir()
    (sessions / "chat-a.openfilm-topic.txt").write_text("own film")
    return tmp_path


@pytest.fixture
def cli(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runner = workspace / "runner"
    runner.write_text(f"#!{sys.executable}\n" + '''
import os
from pathlib import Path
import sys

command, *args = sys.argv[1:]
project = Path.cwd()
if command == "look":
    cache = project / ".film/cache/look"
    cache.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("FRAME_MODE", "new")
    frame = cache / "current frame.png"
    if mode != "cached":
        frame.write_bytes(b"current invocation")
    if mode == "foreign":
        frame = project.parent / "other/.film/cache/look/old.png"
    if mode != "silent":
        print("→ " + str(frame))
    if mode == "fail":
        sys.exit(1)
else:
    (project / "executed.txt").write_text("\\n".join([command, *args]))
''')
    runner.chmod(0o755)
    monkeypatch.setenv("EASEL_OPENFILM", str(runner))
    return workspace


def invoke(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=root,
                          capture_output=True, text=True, timeout=10)


def load_wrapper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("openfilm_wrapper_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("binding", [None, "missing", "outputs/missing", "../outside"])
def test_session_without_valid_binding_never_selects_another_project(workspace: Path, binding: str | None):
    record = workspace / "outputs/_sessions/chat-a.openfilm-topic.txt"
    if binding is None:
        record.unlink()
    else:
        record.write_text(binding)
    assert load_wrapper()._find_project_dir() is None


@pytest.mark.parametrize("topic", ["missing", "own film/missing.mp4", "outputs", "empty"])
def test_invalid_explicit_project_never_falls_back(workspace: Path, topic: str):
    (workspace / "outputs/empty").mkdir()
    assert load_wrapper()._resolve_project(topic) is None


@pytest.mark.parametrize("binding", ["own film", "outputs/own film", "absolute"])
def test_session_binding_supports_existing_path_forms(workspace: Path, binding: str):
    project = workspace / "outputs/own film"
    record = workspace / "outputs/_sessions/chat-a.openfilm-topic.txt"
    record.write_text(str(project) if binding == "absolute" else binding)
    assert load_wrapper()._find_project_dir() == project


def test_standalone_cli_can_find_latest_project(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EASEL_SESSION_KEY")
    project = workspace / "outputs/other"
    os.utime(project, (2_000_000_000, 2_000_000_000))
    assert load_wrapper()._find_project_dir() == project


@pytest.mark.parametrize("owner", [None, "", "另一个账号"])
def test_open_records_missing_account_owner_without_reassigning_existing_project(cli: Path, monkeypatch, owner):
    monkeypatch.setenv("EASEL_PERSONA", "当前账号")
    manifest = cli / "outputs/own film/.easel.json"
    if owner is not None:
        manifest.write_text(json.dumps({"profile": owner, "title": "保留标题", "steps": [{"status": "done"}]}))
    result = invoke(cli, "open", "own film", "--no-browser")
    assert result.returncode == 0, result.stderr
    saved = json.loads(manifest.read_text())
    assert saved["profile"] == (owner or "当前账号")
    if owner is not None:
        assert saved["title"] == "保留标题"
        assert saved["steps"] == [{"status": "done"}]


@pytest.mark.parametrize("command", ["render", "look", "canvas", "get"])
def test_project_commands_fail_before_execution_for_unbound_session(cli: Path, command: str):
    (cli / "outputs/_sessions/chat-a.openfilm-topic.txt").unlink()
    result = invoke(cli, command)
    assert result.returncode == 2
    assert not (cli / "executed.txt").exists()


@pytest.mark.parametrize("command,args", [("render", ["0-2"]), ("canvas", ["apply", "[]"]),
                                          ("get", ["music", "--take", "choice"])])
def test_project_commands_execute_in_bound_project(cli: Path, command: str, args: list[str]):
    result = invoke(cli, command, *args)
    assert result.returncode == 0, result.stderr
    assert (cli / "outputs/own film/executed.txt").read_text() == "\n".join([command, *args])
    assert not (cli / "outputs/other/executed.txt").exists()


@pytest.mark.parametrize("command,args", [("skill", ["edit"]), ("skills", [])])
def test_skill_commands_work_without_a_bound_project(cli: Path, command: str, args: list[str]):
    (cli / "outputs/_sessions/chat-a.openfilm-topic.txt").unlink()
    result = invoke(cli, command, *args)
    assert result.returncode == 0, result.stderr
    assert (cli / "executed.txt").read_text() == "\n".join([command, *args])


@pytest.mark.parametrize("mode,expected", [("new", ["current frame.png"]), ("cached", ["current frame.png"]),
                                          ("fail", []), ("silent", []), ("foreign", [])])
def test_frame_reports_only_successful_current_receipts(cli: Path, monkeypatch: pytest.MonkeyPatch,
                                                       mode: str, expected: list[str]):
    project = cli / "outputs/own film"
    for topic in ("own film", "other"):
        cache = cli / "outputs" / topic / ".film/cache/look"
        cache.mkdir(parents=True)
        (cache / "old.png").write_bytes(b"old invocation")
    cached = project / ".film/cache/look/current frame.png"
    if mode == "cached":
        cached.write_bytes(b"cached current result")
        os.utime(cached, (1, 1))
    monkeypatch.setenv("FRAME_MODE", mode)
    result = invoke(cli, "frame", "2")
    assert result.returncode == (1 if mode == "fail" else 0), result.stderr
    paths = [line.removeprefix("帧：") for line in result.stdout.splitlines() if line.startswith("帧：")]
    assert paths == [str(project / ".film/cache/look" / name) for name in expected]


@pytest.mark.parametrize("command", ["render", "look", "frame"])
def test_explicit_project_override_handles_flags(cli: Path, command: str):
    result = invoke(cli, command, "0-2", "-p", "other")
    assert result.returncode == 0, result.stderr
    if command == "render":
        assert (cli / "outputs/other/executed.txt").read_text() == "render\n0-2"
    else:
        assert (cli / "outputs/other/.film/cache/look/current frame.png").is_file()


@pytest.mark.parametrize("command", ["render", "look", "frame"])
@pytest.mark.parametrize("arguments", [["-p", "other", "--full"], ["--full", "-p", "other"]])
def test_project_flags_work_before_native_options(cli: Path, command: str, arguments: list[str]):
    result = invoke(cli, command, *arguments)
    assert result.returncode == 0, result.stderr
    if command == "render":
        assert (cli / "outputs/other/executed.txt").read_text() == "render\n--full"
    else:
        assert (cli / "outputs/other/.film/cache/look/current frame.png").is_file()


def test_failed_open_preserves_previous_session_binding(workspace: Path):
    wrapper = load_wrapper()
    wrapper._emit_studio_url("http://127.0.0.1:9900/open?to=/projects/failed", 1, "failed")
    record = workspace / "outputs/_sessions/chat-a.openfilm-topic.txt"
    assert record.read_text() == "own film"
