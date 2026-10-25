"""Easel 画像（Profile）共享助手 —— CLI / Web / skill 三入口的单一真相源。

历史上三处各写一份画像逻辑，且 CLI 还残留全局 USER.md（并发竞态）。
统一到这里后：画像一律**作为消息内联**注入（`我当前使用的画像是「X」。`），
由当前 Agent 执行引擎按 AGENTS.md 自行读取 `profiles/<X>/` 并凝练，每个请求自包含，无全局文件污染。
参见 docs/prompt-stack.md。
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = PROJECT_ROOT / "profiles"

# 六维画像文件的固定顺序（identity/style/... 先，其余 .md 追加在后）
_FILE_ORDER = [
    "identity.md", "style.md", "audience.md",
    "platforms.md", "preferences.md", "memory.md",
]


def list_personas() -> list[str]:
    """列出所有可用画像目录名（排除下划线开头的内部目录）。"""
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        d.name for d in PROFILES_DIR.iterdir()
        if d.is_dir() and valid_persona_name(d.name)
    )


def profile_exists(name: str) -> bool:
    """检查画像目录是否存在，拒绝把路径片段当作画像名。"""
    return valid_persona_name(name) and (PROFILES_DIR / name).is_dir()


def valid_persona_name(name: str) -> bool:
    """Return whether ``name`` is a single, user-facing profile directory name."""
    return (
        isinstance(name, str)
        and bool(name)
        and not name.startswith((".", "_"))
        and not any(character in name for character in ("/", "\\", ":"))
        and not any(ord(character) < 32 for character in name)
    )


def load_profile_text(name: str) -> str:
    """读取画像文件夹，按固定顺序拼接所有非空 .md。画像不存在返回空串。"""
    if not valid_persona_name(name):
        return ""
    profile_dir = PROFILES_DIR / name
    if not profile_dir.is_dir():
        return ""
    parts: list[str] = []
    for filename in _FILE_ORDER:
        filepath = profile_dir / filename
        if filepath.is_file():
            text = filepath.read_text(encoding="utf-8").strip()
            if text:
                parts.append(text)
    for filepath in sorted(profile_dir.glob("*.md")):
        if filepath.name not in _FILE_ORDER:
            text = filepath.read_text(encoding="utf-8").strip()
            if text:
                parts.append(text)
    return "\n\n---\n\n".join(parts)


def persona_prefix(name: str | None) -> str:
    """把画像作为消息前缀内联。无画像或画像不存在时返回空串。

    明确账号记忆作用域，避免工作区全局 MEMORY.md 污染并行画像会话。
    """
    if name and profile_exists(name):
        return (
            f"我当前使用的画像是「{name}」。"
            f"本会话的账号长期记忆仅使用 profiles/{name}/memory.md，"
            "不要使用工作区全局 MEMORY.md 作为账号记忆。"
        )
    return ""


# --- 每轮行为提醒（抗长对话指令衰减）----------------------------------------
# AGENTS.md / SOUL.md 是系统提示，只在会话开头最「新鲜」；长对话里后续追问，
# 模型对系统提示的注意力会衰减 → 常见现象「开头会查 SKILL、后面就凭记忆裸做」。
# 把最关键的反射每轮在消息末尾重申一次（放末尾借近因效应），成本极低，
# 显著提升后续轮次的 SKILL 命中率。仅用于对话入口；单跑某个 SKILL 不必加。
TURN_REMINDER = (
    "〔内部提醒·非用户所说，勿复述、勿回显〕本轮动手前先查技能库："
    "有对应或相邻的 SKILL 就读进来、按它的流程/数据源/工具做，别凭记忆或通用知识裸做；"
    "五层（含制作层：图文/图/视频/成片/长稿等）都由你自己按对应 SKILL 产出成品文件到 outputs/；"
    "问「我的账号/帖子/粉丝/最近发了啥」先查已登录账号、别回问用户要账号名；"
    "要发到公开平台的文案/评论绝不写入密钥/内部地址/代理/路径/env 名等敏感信息，也别随手自曝「由 AI 生成/某工具做的」。"
)


def turn_reminder() -> str:
    """返回每轮行为提醒文案。"""
    return TURN_REMINDER


def chat_turn_message(user_message: str, name: str | None) -> str:
    """构造发给 Agent 底座的一轮对话消息：画像前缀（如有）+ 用户原文 + 末尾行为提醒。

    末尾提醒对抗长对话里「忘记先查 SKILL」的指令衰减（见 TURN_REMINDER）。
    对用户不可见（前端只显示用户原文），只进底座上下文。
    """
    prefix = persona_prefix(name)
    head = f"{prefix}\n\n" if prefix else ""
    return f"{head}{user_message}\n\n{turn_reminder()}"


# --- 知识库（外部内容源）聚焦：画像 → glob 范围，查询/沉淀随画像而不同 --------
def _persona_kb_scope(persona: str | None) -> list[str]:
    """当前画像对「外部内容源/知识库」的聚焦 glob 列表（相对源根）。
    来源 profiles/kb_scopes.json；缺省/非法/该画像无配置 → 空（不聚焦，通用显示全库）。
    每个条目支持精确名、前缀（如 02-技术沉淀）、或 glob（如 03-新闻资讯/06-*）。"""
    if not persona or not valid_persona_name(persona) or not profile_exists(persona):
        return []
    fp = PROFILES_DIR / "kb_scopes.json"
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    entry = data.get(persona)
    if isinstance(entry, list):
        return [str(x).strip() for x in entry if isinstance(x, str) and x.strip()]
    return []


def _kb_in_scope(rel: str, patterns: list[str]) -> bool:
    """rel 相对源根的路径是否在 patterns 聚焦范围内。
    patterns 为空 → 全放行（通用）；否则命中精确/前缀/glob，或作为某命中路径的祖先，才保留。"""
    if not patterns:
        return True
    for pat in patterns:
        if pat in ("", "*"):
            return True
        if rel == pat or rel.startswith(pat + "/"):
            return True
        if pat.startswith(rel + "/"):   # rel 是某命中路径的祖先 → 保留以便下钻
            return True
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


# --- 技能库聚焦：画像 → 技能 glob 范围（与知识库同源思路，查询/可见随画像而不同）---
# 语义 A1（与 KB 一致）：scope 为空 = 全量（通用/无画像/该画像未配置）；非空 = 仅命中。
# 技能按「目录名」匹配：支持精确名、前缀（如 skill-）、或 glob（如 skill-xhs-*）。
def _persona_skill_scope(persona: str | None) -> list[str]:
    """当前画像对「技能库」的聚焦 glob 列表。来源 profiles/skill_scopes.json。
    缺省/非法/该画像无配置 → 空（不聚焦，通用显示全库）。"""
    if not persona or not valid_persona_name(persona) or not profile_exists(persona):
        return []
    fp = PROFILES_DIR / "skill_scopes.json"
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    entry = data.get(persona)
    if isinstance(entry, list):
        return [str(x).strip() for x in entry if isinstance(x, str) and x.strip()]
    return []


def _skill_in_scope(name: str, patterns: list[str]) -> bool:
    """技能目录名 name 是否在 patterns 聚焦范围内。
    patterns 为空 → 全放行（通用）；否则命中精确名/前缀/glob 才保留。"""
    if not patterns:
        return True
    for pat in patterns:
        if pat in ("", "*"):
            return True
        # 精确名/前缀（pat 可为 "skill-xhs" 或 "skill-xhs-"）或 glob（如 skill-xhs-*）
        if name.startswith(pat) or fnmatch.fnmatch(name, pat):
            return True
    return False
