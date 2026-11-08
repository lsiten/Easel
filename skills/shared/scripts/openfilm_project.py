"""Project-bound OpenFilm commands and successful look output receipts."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from typing import Final, Protocol

TIME_SPEC: Final = re.compile(r"^\d+(?:\.\d+)?s?(?:-\d+(?:\.\d+)?s?)?$")
PROJECT_COMMANDS: Final = frozenset({"frame", "look", "render", "canvas", "get"})


class Runner(Protocol):
    def __call__(self, *args: str, cwd: Path | None = None,
                 timeout: int | None = None) -> tuple[int, str, tuple[str, ...] | None]: ...


def project_path(root: Path, topic: str) -> Path:
    """Normalize the topic and outputs-prefixed forms accepted by open."""
    path = Path(topic).expanduser()
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] == "outputs":
        return (root / path).resolve()
    return (root / "outputs" / path).resolve()


def resolve_project(root: Path, topic: str | None) -> Path | None:
    """An explicit existing target selects its project, with no fallback on a typo."""
    if not topic or not topic.strip():
        return find_project(root)
    target = project_path(root, topic.strip())
    if not target.exists():
        return None
    for candidate in (target, *target.parents):
        if (candidate / "film.html").is_file():
            return candidate
    return None


def find_project(root: Path) -> Path | None:
    """Session bindings are authoritative. Standalone use retains latest-project discovery."""
    outputs = (root / "outputs").resolve()
    session = os.environ.get("EASEL_SESSION_KEY", "").strip()
    if session:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:120]
        record = outputs / "_sessions" / f"{safe}.openfilm-topic.txt"
        try:
            topic = record.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not topic:
            return None
        target = project_path(root, topic)
        if target == outputs or not target.is_relative_to(outputs):
            return None
        return target if (target / "film.html").is_file() else None
    if not outputs.is_dir():
        return None
    projects = [path for path in outputs.iterdir()
                if not path.name.startswith(("_", ".")) and (path / "film.html").is_file()]
    return max(projects, key=lambda path: path.stat().st_mtime, default=None)


def command_target(root: Path, args: argparse.Namespace) -> tuple[Path | None, list[str]]:
    """Consume wrapper project flags while preserving native command arguments."""
    forwarded = list(args.args or [])
    topic = getattr(args, "project", None)
    for flag in ("--project", "-p"):
        if flag in forwarded:
            index = forwarded.index(flag)
            if index + 1 == len(forwarded) or forwarded[index + 1].startswith("-"):
                return None, []
            topic = forwarded[index + 1]
            del forwarded[index:index + 2]
    explicit = bool(topic)
    target = None
    if args.cmd in {"frame", "look", "render"} and forwarded:
        first = forwarded[0]
        if not first.startswith("-") and not TIME_SPEC.fullmatch(first):
            target = project_path(root, first)
            if explicit and not Path(first).is_absolute() and not first.startswith("outputs/"):
                target = project_path(root, topic) / first
            elif not explicit and not target.exists():
                bound = find_project(root)
                if bound is not None and (bound / first).exists():
                    target = (bound / first).resolve()
            if not target.exists():
                return None, []
            topic = str(target) if not explicit else topic
            forwarded.pop(0)
    project = resolve_project(root, topic)
    if project is None:
        return None, forwarded
    if target is not None:
        if target.is_dir() and not target.is_relative_to(project):
            return None, []
        forwarded.insert(0, str(target))
    return project, forwarded


def frame_paths(project: Path, output: str) -> list[Path]:
    """Read the native look command's explicit PNG receipts, including valid cache hits."""
    cache = (project / ".film/cache/look").resolve()
    frames: list[Path] = []
    for line in output.splitlines():
        if not line.strip().startswith("→ "):
            continue
        path = Path(line.strip()[2:].strip()).expanduser()
        frame = (project / path).resolve()
        if (frame.is_relative_to(cache) and frame.suffix.lower() == ".png"
                and frame.is_file() and frame not in frames):
            frames.append(frame)
    return frames


def run_command(root: Path, args: argparse.Namespace, runner: Runner) -> int:
    """Run project commands in their resolved project and expose only successful frame receipts."""
    if args.cmd not in PROJECT_COMMANDS:
        cwd = (find_project(root) or root) if args.cmd in {"skill", "skills"} else root
        rc, output, _ = runner(args.cmd, *(args.args or []), cwd=cwd, timeout=None)
        sys.stdout.write(output)
        return rc
    project, forwarded = command_target(root, args)
    if project is None:
        print("未找到电影项目（无 film.html）；请先 `openfilm.py open <主题>` 或传 -p 主题。", file=sys.stderr)
        return 2
    command = "look" if args.cmd == "frame" else args.cmd
    from openfilm_music import MusicRequest, prepare_music
    music = prepare_music(MusicRequest(root, project, command, tuple(forwarded)))
    for line in music.lines:
        print(line)
    if music.handled:
        return music.code
    rc, output, _ = runner(command, *music.arguments, cwd=project, timeout=None)
    sys.stdout.write(output)
    if command == "render" and rc == 0:
        final_music = prepare_music(MusicRequest(root, project, command, tuple(forwarded)))
        if final_music.code:
            for line in final_music.lines:
                print(line)
            print("导出后的配乐检查失败；请修复时间线并重新导出，此文件不作为通过验收的成片。", file=sys.stderr)
            return final_music.code
    if args.cmd == "frame" and rc == 0:
        frames = frame_paths(project, output)
        if frames:
            print("\n[帧 · 可 view_image 查看，用于自检]")
            for frame in frames:
                print(f"帧：{frame}")
    return rc
