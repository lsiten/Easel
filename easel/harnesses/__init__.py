"""Harness registry for pluggable agent engines."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .acp import ACPAdapter, OpenCodeAdapter
from .base import Descriptor, HarnessError, RunRequest
from .codex import CodexAdapter
from .pi import PiAdapter
from .commands import acp_command, base_command
from .store import settings

CHAT_ONLY = Descriptor("api", "仅聊天模式", "模型 API 直连", "在模型配置中填写地址、模型与 Key")
ADAPTERS = {adapter.descriptor.id: adapter for adapter in (OpenCodeAdapter(), CodexAdapter(), ACPAdapter(), PiAdapter())}


def adapter(runtime: str):
    if runtime not in ADAPTERS:
        raise HarnessError("此 Agent 底座未注册或需 API 直连（仅聊天模式），请在设置中选择已支持的底座")
    return ADAPTERS[runtime]


def describe(runtime: str) -> dict:
    descriptor = CHAT_ONLY if runtime == "api" else adapter(runtime).descriptor
    from dataclasses import asdict
    result = asdict(descriptor)
    try:
        if runtime == "api":
            pass
        elif runtime == "acp":
            acp_command(Path(__file__).resolve().parents[2])
        else:
            base_command(runtime)
        result["installed"] = True
    except (HarnessError, OSError):
        result["installed"] = False
    return result


async def console_turn(prompt: str, root: Path, env: dict, native_id="", *,
                       timeout=1800, interactive=True, on_bind=None) -> tuple[str, str]:
    config = settings(root / "outputs", env)
    runtime = config["runtime"]
    full_text = []
    identifiers = [native_id]

    def emit(kind, text):
        if kind == "token":
            full_text.append(text)
            print(text, end="", flush=True)
        elif kind == "activity":
            print(f"\n{text}", flush=True)

    async def ask(questions):
        if not interactive:
            raise HarnessError("请在交互式对话中确认工具权限")
        answers = {}
        for question in questions:
            print("\n" + question["question"])
            for option in question.get("options") or []:
                print("  " + option["label"])
            answer = await asyncio.to_thread(input, "回答（授权只允许选择列出的选项）：")
            answers[question["questionId"]] = [answer]
        return answers

    run_env = {**env, "EASEL_AGENT_RUNTIME": runtime}
    # 模型未显式配置时，按该底座的原生模型目录解析，避免落到引擎自带、
    # 本机不可达的默认模型（如 pi 默认的 claude/api.anthropic.com）。
    model = config["models"].get(runtime, "")
    if not model and runtime in ("pi", "codex", "opencode"):
        from .models import resolve_effective_model
        try:
            model = await resolve_effective_model(
                runtime, "",
                RunRequest("", root, run_env, "", "", "", 25))
        except Exception:
            pass
    request = RunRequest(prompt, root, run_env, native_id,
                         model, config["sandbox"], timeout,
                         codex_auto_approve=config["codex_auto_approve"])

    def bind(value):
        identifiers[0] = value
        if on_bind:
            on_bind(value)

    await asyncio.wait_for(adapter(runtime).run(request, emit, ask,
                                                bind), request.timeout)
    return "".join(full_text), identifiers[0]
