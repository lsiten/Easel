#!/usr/bin/env python3
"""Remove environment components that Easel can safely identify and recreate."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import install_tool

REMOVE_MODEL_PY = """
import os, shutil, sys
path = os.path.expanduser('~/models/whisper-large-v3')
if os.path.islink(path):
    print('拒绝删除符号链接模型目录', file=sys.stderr)
    raise SystemExit(2)
if os.path.isdir(path):
    shutil.rmtree(path)
"""

REMOVE_PROJECT_PATH_PY = """
import os, shutil, sys
base = os.path.realpath(sys.argv[1])
target = os.path.realpath(os.path.join(base, sys.argv[2]))
if os.path.commonpath((base, target)) != base:
    print('拒绝删除工程目录之外的路径', file=sys.stderr)
    raise SystemExit(2)
if os.path.islink(target):
    os.unlink(target)
elif os.path.isdir(target):
    shutil.rmtree(target)
"""

REMOVE_PLAYWRIGHT_PY = """
import subprocess, sys
subprocess.run([sys.executable, '-m', 'playwright', 'uninstall', 'chromium'], check=False)
raise SystemExit(subprocess.run(
    [sys.executable, '-m', 'pip', 'uninstall', '-y', 'playwright']
).returncode)
"""


@dataclass(frozen=True, slots=True)
class UninstallResult:
    id: str
    state: Literal["ok", "fail"]
    version: str | None = None
    strategy: str | None = None
    detail: str | None = None


def _pip_remove(*packages: str) -> list[str]:
    return ["{python}", "-m", "pip", "uninstall", "-y", *packages]


UNINSTALL_STEPS: dict[str, list[tuple[str, list[str], int]]] = {
    "fw": [("卸载 faster-whisper", _pip_remove("faster-whisper"), 600)],
    "model": [("删除 Whisper large-v3 模型", ["{python}", "-c", REMOVE_MODEL_PY], 120)],
    "rmdeps": [("删除 node_modules", [
        "{python}", "-c", REMOVE_PROJECT_PATH_PY, "{dir}", "node_modules"], 120)],
    "shell": [("删除 Chrome Headless Shell 缓存", [
        "{python}", "-c", REMOVE_PROJECT_PATH_PY, "{dir}", "node_modules/.remotion"], 120)],
    "biliup": [
        ("卸载 @biliup/cli", ["npm", "uninstall", "-g", "@biliup/cli"], 600),
        ("清理旧 Python 包", _pip_remove("biliup"), 600),
    ],
    "pw": [("卸载 Playwright 与 Chromium", ["{python}", "-c", REMOVE_PLAYWRIGHT_PY], 1200)],
    "pylibs": [("卸载常用 Python 库", _pip_remove(
        "Pillow", "edge-tts", "pdfplumber", "requests"), 600)],
}


def uninstall_tool(tool: install_tool.Tool, python: str,
                   dir_: str | None = None) -> UninstallResult:
    """Run one removal recipe and verify the component is actually gone."""
    if not tool.uninstallable:
        install_tool._die(f"{tool.id} 是系统或外部组件，不支持由 Easel 卸载", 3)
    if tool.needs_dir and not dir_:
        install_tool._die(f"{tool.id} 需要 --dir <工程目录>（{tool.desc}）", 3)
    if tool.needs_dir and not (Path(dir_) / "package.json").is_file():
        install_tool._die(f"{dir_} 里没有 package.json，不是 Remotion 工程目录", 3)
    pre = install_tool.check_tool(tool, python, dir_)
    managed_model_exists = tool.id == "model" and (Path.home() / "models" / "whisper-large-v3").is_dir()
    if pre["state"] != "ok" and not managed_model_exists:
        print(f"· {tool.name} 未安装，无需卸载", file=sys.stderr)
        return UninstallResult(id=tool.id, state="ok", strategy="未安装")

    print(f"▶ 卸载 {tool.name}", file=sys.stderr)
    last_err = ""
    for label, argv, timeout in UNINSTALL_STEPS[tool.id]:
        argv_f = install_tool._fill(argv, python, dir_)
        print(f"  → 操作：{label}", file=sys.stderr)
        try:
            proc = install_tool._run(argv_f, cwd=dir_ if tool.needs_dir else None,
                                     timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            last_err = f"{label}: {type(error).__name__}: {error}"
            print(f"    ✗ {last_err}", file=sys.stderr)
            continue
        if proc.returncode != 0:
            tail = install_tool._err_tail(proc)
            last_err = f"{label}: 退出码 {proc.returncode}" + (f" · {tail}" if tail else "")
            print(f"    ✗ {last_err}", file=sys.stderr)
            continue
        print(f"    ✓ {label}", file=sys.stderr)

    state = install_tool.check_tool(tool, python, dir_)
    if state["state"] != "ok":
        return UninstallResult(id=tool.id, state="ok", strategy="已卸载")
    return UninstallResult(id=tool.id, state="fail",
                           detail=last_err or "卸载后仍检测到组件")


def cmd_uninstall(args) -> int:
    python = args.python or install_tool.resolve_python()
    tools = {tool.id: tool for tool in install_tool.TOOLS}
    unknown = [tool_id for tool_id in args.ids if tool_id not in tools]
    if unknown:
        install_tool._die(f"不认识的工具：{'、'.join(unknown)}", 3)
    results = [uninstall_tool(tools[tool_id], python, args.dir) for tool_id in args.ids]
    if args.json:
        print(json.dumps(
            {"python": python, "results": [asdict(result) for result in results]},
            ensure_ascii=False,
        ))
    return 0 if all(result.state == "ok" for result in results) else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command")
    remove = commands.add_parser("uninstall")
    remove.add_argument("ids", nargs="+")
    remove.add_argument("--dir")
    remove.set_defaults(func=cmd_uninstall)
    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
