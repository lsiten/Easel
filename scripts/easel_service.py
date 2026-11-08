#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Manage a macOS launchd service so Easel survives the invoking chat/shell.

Run: .venv/bin/python scripts/easel_service.py start|restart|status|stop
Only executable paths and PATH are persisted; model credentials stay in Easel settings.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
LABEL: Final = "io.easel.web." + hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
DOMAIN: Final = f"gui/{os.getuid()}" if hasattr(os, "getuid") else ""
PLIST: Final = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def loaded() -> bool:
    return subprocess.run(["/bin/launchctl", "print", f"{DOMAIN}/{LABEL}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0


def stop() -> None:
    if loaded():
        subprocess.run(["/bin/launchctl", "bootout", f"{DOMAIN}/{LABEL}"], check=True, capture_output=True)
        deadline = time.monotonic() + 15
        while loaded() and time.monotonic() < deadline:
            time.sleep(0.1)
        if loaded():
            raise TimeoutError("launchd 尚未卸载 Easel 服务，请稍后重试")
    PLIST.unlink(missing_ok=True)


def start(port: int) -> int:
    if loaded():
        print(f"Easel 服务已加载：http://localhost:{port}；需要重载代码时使用 restart")
        return 0 if listening(port) else 1
    if listening(port):
        print(f"端口 {port} 已被其他进程占用，未启动重复服务", file=sys.stderr)
        return 1
    python = ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        print("缺少项目 Python 环境，请先运行项目安装流程", file=sys.stderr)
        return 1
    logs = ROOT / "outputs" / "_run"
    logs.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(ROOT / "web" / "app.py")],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
                                 "EASEL_PORT": str(port), "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "StandardOutPath": str(logs / "easel-service.log"),
        "StandardErrorPath": str(logs / "easel-service-error.log"),
    }
    with PLIST.open("wb") as stream:
        plistlib.dump(config, stream)
    subprocess.run(["/bin/launchctl", "bootstrap", DOMAIN, str(PLIST)], check=True, capture_output=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if listening(port):
            print(f"Easel 服务已启动：http://localhost:{port}")
            print(f"日志：{logs / 'easel-service-error.log'}")
            return 0
        time.sleep(0.25)
    print(f"Easel 尚未开始监听，请查看 {logs / 'easel-service-error.log'}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "restart", "status", "stop"))
    parser.add_argument("--port", type=int, default=7860)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("此常驻管理器用于 macOS；其他平台请使用 easel web 或系统服务管理器")
    match args.action:
        case "stop":
            stop()
            print("Easel 常驻服务已停止并取消登录启动")
            return 0
        case "status":
            ready = loaded() and listening(args.port)
            print(f"{'运行中' if ready else '未运行'}：http://localhost:{args.port}")
            return 0 if ready else 1
        case "restart":
            stop()
            return start(args.port)
        case "start":
            return start(args.port)
        case _:
            parser.error("未知操作")


if __name__ == "__main__":
    raise SystemExit(main())
