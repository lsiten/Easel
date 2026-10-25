"""探测本机已装的 AI agent CLI，供界面如实展示「本机有什么」。

为什么需要：Easel 支持多个 Agent 底座（Codex / OpenCode / ACP / Pi）以及
「仅聊天模式」（模型 API 直连）。本机常常已经装着 Claude Code、Gemini CLI、
Codex、OpenCode 这类 agent CLI —— 探测出来是为了在界面上如实展示「本机有什么、
哪些已安装」，而不是假装支持。

"""

from __future__ import annotations

import shutil

# 已知的 agent CLI：id 与展示名、探测命令、一句话登录提示。
KNOWN_AGENT_CLIS: tuple[dict[str, object], ...] = (
    {
        "id": "claude-code",
        "label": "Claude Code",
        "commands": ("claude",),
        "login_hint": "在终端运行 claude 完成登录即可使用。",
    },
    {
        "id": "gemini-cli",
        "label": "Gemini CLI",
        "commands": ("gemini",),
        "login_hint": "在终端运行 gemini 完成登录即可使用。",
    },
    {
        "id": "codex",
        "label": "Codex CLI",
        "commands": ("codex",),
        "login_hint": "在终端运行 codex 完成登录即可使用。",
    },
    {
        "id": "opencode",
        "label": "OpenCode",
        "commands": ("opencode",),
        "login_hint": "在终端运行 opencode 完成登录即可使用。",
    },
    {
        "id": "qwen-code",
        "label": "Qwen Code",
        "commands": ("qwen",),
        "login_hint": "在终端运行 qwen 完成登录即可使用。",
    },
    {
        "id": "copilot-cli",
        "label": "GitHub Copilot CLI",
        "commands": ("copilot",),
        "login_hint": "Copilot 通过本地代理（默认 localhost:3000）接入，需另行运行代理服务。",
    },
)


def detect_local_agents() -> list[dict[str, object]]:
    """扫一遍 PATH，返回每个已知 agent CLI 的探测结果。

    字段：
      id / label      标识与展示名
      installed       是否在 PATH 上找到
      command / path  命中的可执行名与绝对路径
      loginHint       给用户的一句话说明
    """
    out: list[dict[str, object]] = []
    for spec in KNOWN_AGENT_CLIS:
        found_cmd = ""
        found_path = ""
        for cmd in spec["commands"]:  # type: ignore[union-attr]
            p = shutil.which(cmd)  # type: ignore[arg-type]
            if p:
                found_cmd, found_path = cmd, p
                break
        out.append({
            "id": spec["id"],
            "label": spec["label"],
            "installed": bool(found_path),
            "command": found_cmd,
            "path": found_path,
            "loginHint": spec["login_hint"],
        })
    return out


def summarize_local_agents() -> dict[str, object]:
    """给界面/doctor 用的摘要：本机装了哪些 agent CLI。"""
    agents = detect_local_agents()
    installed = [a for a in agents if a["installed"]]
    return {
        "agents": agents,
        "installedCount": len(installed),
    }
