"""easel ping — 连通性测试（直接运行，不依赖 Docker）。"""

from __future__ import annotations

import asyncio

from easel.direct_api import DirectAPIError, read_settings, stream_chat
from easel import harnesses
from easel.harnesses.base import HarnessError

GREEN = "\033[0;32m"
RED = "\033[0;31m"
NC = "\033[0m"


def cmd_ping(_args) -> int:
    print("[easel] 连通性测试\n")
    settings = read_settings()
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    runtime = harnesses.settings(root / "outputs", settings)["runtime"]

    if runtime == "api":
        async def probe():
            async for kind, text in stream_chat(settings, "请只回复 PONG", timeout=30):
                if kind == "token":
                    print(text, end="", flush=True)
        try:
            asyncio.run(asyncio.wait_for(probe(), timeout=35))
        except (DirectAPIError, asyncio.TimeoutError) as error:
            print(f"\n{RED}API 直连 FAIL{NC}：{error or '请求超时'}")
            return 1
        print(f"\n{GREEN}✓ API 直连通过{NC}")
        return 0

    # 其他 Agent 底座（Codex / OpenCode / ACP / Pi）：跑一轮只回复 PONG 验证连通性
    try:
        asyncio.run(asyncio.wait_for(
            harnesses.console_turn("请只回复 PONG，不执行任何工具", root, settings,
                                  timeout=60, interactive=False), 60))
        print(f"\n{GREEN}✓ {runtime} 连通性测试通过{NC}")
        return 0
    except (HarnessError, asyncio.TimeoutError) as error:
        print(f"\n{RED}{runtime} 连通性失败：{error or '请求超时'}{NC}")
        return 1
