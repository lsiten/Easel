"""Easel Web — FastAPI 后端（含 SSE 流式输出）."""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
if os.name == "nt":
    import msvcrt
else:
    import fcntl
import hashlib
import ipaddress
import json
import re
import shutil
import fnmatch
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from contextlib import asynccontextmanager
import ipaddress
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool
from sse_starlette.sse import EventSourceResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from easel.persona import (load_profile_text, persona_prefix, chat_turn_message,
                           profile_exists, valid_persona_name, _FILE_ORDER,
                           _persona_kb_scope, _kb_in_scope,
                           _persona_skill_scope, _skill_in_scope)
from easel.timeouts import TIMEOUT_CHAT, TIMEOUT_DIRECT, TIMEOUT_PRODUCE
from easel.manual_publish import build_package, publishing_mode
from easel.direct_api import (DirectAPIError, SYSTEM_PROMPT, api_config, api_mode,
                              history_path, read_settings, stream_chat)
from easel import harnesses
from easel.harnesses.models import resolve_effective_model
from easel import kb
from easel.harnesses import store as harness_store
from easel.harnesses.base import HarnessError, RunRequest
PROFILES_DIR = PROJECT_ROOT / "profiles"
SKILLS_DIR = PROJECT_ROOT / "skills"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

STATIC_DIR = Path(__file__).resolve().parent / "static"
REACT_DIR = Path(__file__).resolve().parent / "frontend" / "dist"


# 思考档位（每轮 --thinking）。前后端已完整支持展示思考：后端把 thinking_delta 转成 SSE
# `thinking` 事件，前端 MessageBubble 渲染「💭 思考过程」并在流式结束后持久保留。面板里有没有
# 内容取决于网关——支持 extended-thinking 的网关会按档位回传思考流；不支持的（如内网 codewiz，
# 实测 transcript 里 assistant 只有 text 块、thinking 恒为 0）面板留空，调高档位也不会有内容。
# 默认 medium：让支持思考的部署直接显示较完整思考；EASEL_THINKING_LEVEL 可覆盖（low 提速 / high 更详尽）。
THINKING_LEVEL = (os.environ.get("EASEL_THINKING_LEVEL", "").strip() or "medium")



class _GatewayHttpProc:
    """HTTP 直连模式下的「伪进程」：给主循环 / 停止逻辑提供 poll/wait/kill 兼容面。"""

    def __init__(self) -> None:
        self._done = False
        self._task = None

    def finish(self) -> None:
        self._done = True

    def poll(self):
        return 0 if self._done else None

    def terminate(self) -> None:
        self._done = True
        if self._task is not None:
            try:
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):  # 兼容 await asyncio.to_thread(proc.wait, ...)
        # 必须真的等：主循环拿它来决定「能不能放掉会话双锁」。提前返回会让下一轮在
        # 上一轮可能还在写同一份 transcript 时就启动 —— 正是双锁要防的并发写。
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._done:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("gateway-http-turn", timeout)
            time.sleep(0.05)
        return 0


# 制作层/直接执行层/chat 超时统一走 easel/timeouts.py（CLI/Web/skill 三入口单一真相源）

SHARED_SCRIPTS = PROJECT_ROOT / "skills" / "shared" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from model_registry import model_group

BROWSER_PROFILES = Path.home() / ".easel-browser-profiles"
LOGIN_DIR = OUTPUTS_DIR / "_login"
PUBLISH_DIR = OUTPUTS_DIR / "_publish"   # 异步发布的状态/验证码文件（抖音发布可能触发短信墙）
PROFILE_BUILD_DIR = OUTPUTS_DIR / "_profile_build"   # 异步画像构建的状态文件（避免长请求被代理超时）
DEBUG_DIR = OUTPUTS_DIR / "_debug"   # 诊断日志（对话流收尾情况等），_ 前缀不进内容库
SESSIONS_DIR = OUTPUTS_DIR / "_sessions"   # 每会话最近一轮的完整结果，供 SSE 连接中断后前端取回

def _session_exists(sessions_dir, key: str) -> bool:
    """该 plain session 是否已有历史/绑定/turn/transport → 用于 legacy 兼容：已有历史则不改名。"""
    if not key:
        return False
    try:
        if harness_store.binding_path(sessions_dir, key).is_file():
            return True
        if history_path(sessions_dir, key).is_file():
            return True
        if _turn_file(f"web:{key}").is_file():
            return True
    except Exception:
        pass
    return False


def _eff_sk(session_id: str | None, persona: str | None) -> str:
    """画像级隔离的会话 key：有合法 persona → 始终折入画像名（硬隔离，与 base 是否已有
    通用历史无关）；无/非法 persona（通用模式）→ 原 id。

    说明：早期版本在 base 已有历史时回退原名（legacy 兼容），但这会让「先通用后切画像」
    的同一 plain id 在所有画像下都落到 base，画像隔离失效。现改为：只要 persona 合法就折入，
    通用/无画像仍走 base（向后兼容全局会话）。不同画像 → 不同 transcript/绑定/历史。
    """
    base = (session_id or "").strip()
    if not base:
        base = f"web-{int(time.time() * 1000)}"
    if persona and valid_persona_name(persona) and profile_exists(persona):
        safe = re.sub(r"[^A-Za-z0-9_.\u4e00-\u9fff]", "_", persona)[:48]
        return f"{safe}::{base}"
    return base
# 非 _ 前缀的历史系统目录（归因层数据），内容库不展示（真产物一律在项目目录内）
SYSTEM_TOPLEVEL_DIRS = {"analytics"}
LOGIN_TIMEOUT = 240
LOGIN_PROCESSES: dict[str, subprocess.Popen] = {}

# whoami 真校验（起 headless 浏览器，数秒）的进程内缓存：避免账号页 + 工作台重复起浏览器。
WHOAMI_TTL = 600  # 秒
_WHOAMI_CACHE: dict[str, tuple[float, dict]] = {}
_WHOAMI_LOCK = threading.Lock()
_WHOAMI_TASKS: dict[str, asyncio.Task] = {}
_LOGIN_START_LOCKS: dict[str, asyncio.Lock] = {}

LOGIN_RUNNERS: dict[str, dict] = {
    "xiaohongshu": {"name": "小红书", "backend": "xhs", "profile": "XiaohongshuProfile"},
    "kuaishou": {"name": "快手", "backend": "web", "wp": "kuaishou", "profile": "KuaishouProfile"},
    "weixin-channels": {"name": "微信视频号", "backend": "web", "wp": "weixin-channels", "profile": "ChannelsProfile"},
    "zhihu": {"name": "知乎", "backend": "web", "wp": "zhihu", "profile": "ZhihuProfile"},
    "csdn": {"name": "CSDN", "backend": "web", "wp": "csdn", "profile": "CsdnProfile"},
    "juejin": {"name": "掘金", "backend": "web", "wp": "juejin", "profile": "JuejinProfile"},
    "bilibili": {"name": "B站", "backend": "biliup"},
    "douyin": {"name": "抖音", "backend": "douyin", "profile": "DouyinProfile"},
    # 微信公众号：扫码登录后台会话（发布+数据都走它），backend=='wechat-oa' 在各处单独分支处理。
    "wechat-oa": {"name": "微信公众号", "backend": "wechat-oa"},
}

# ---- 按画像完全隔离登录态（Option B）----
# 每个画像拥有独立的登录状态 + cookie/浏览器 profile；通用模式(空/非法画像)沿用历史全局位置，向后兼容。
def _valid_login_persona(persona: str | None) -> bool:
    return bool(persona) and valid_persona_name(persona) and profile_exists(persona)

def _login_dir(persona: str | None) -> Path:
    """登录状态目录：命名画像 outputs/_login/<画像>/；通用沿用 outputs/_login/（向后兼容）。"""
    return (LOGIN_DIR / persona) if _valid_login_persona(persona) else LOGIN_DIR

def _login_dir_rel(persona: str | None) -> str:
    """登录状态目录相对 outputs 的路径串（前端 mediaUrl 取二维码/日志用）。"""
    return f"_login/{persona}" if _valid_login_persona(persona) else "_login"

def _persona_profile_base(persona: str | None) -> Path:
    """浏览器登录态根目录：命名画像 ~/.easel-browser-profiles/<画像>/；通用沿用 ~/.easel-browser-profiles/。"""
    return (BROWSER_PROFILES / persona) if _valid_login_persona(persona) else BROWSER_PROFILES

def _persona_cookie_file(persona: str | None) -> Path:
    """biliup cookie 文件：命名画像 <profile-base>/cookies.json；通用沿用 PROJECT_ROOT/cookies.json。"""
    if _valid_login_persona(persona):
        return _persona_profile_base(persona) / "cookies.json"
    return PROJECT_ROOT / "cookies.json"

def _pk(persona: str | None, platform: str) -> str:
    """进程/锁/缓存的复合键：按 (画像, 平台) 完全隔离。"""
    return f"{(persona or '_general')}::{platform}"

# ---- 微信公众号（wechat-oa）凭证式接入 ----
# 复用 skill-wechat-publisher 的发布引擎与配置：凭证存在其 wechat-publisher.yaml，
# 发布/取数脚本都从这里读账号。web 侧统一用账号 key "web"。
WECHAT_SKILL_DIR = PROJECT_ROOT / "skills" / "builtin" / "skill-wechat-publisher"
WECHAT_SKILL_SCRIPTS = WECHAT_SKILL_DIR / "scripts"
WECHAT_PUBLISH_SCRIPT = WECHAT_SKILL_SCRIPTS / "publish.py"
WECHAT_CONFIG_YAML = WECHAT_SKILL_DIR / "wechat-publisher.yaml"
WECHAT_WEB_ACCOUNT = "web"   # web 端配置写入/读取的账号 key


def _wechat_load_yaml() -> dict:
    """读 wechat-publisher.yaml（不存在或损坏则返回空 dict）。"""
    if not WECHAT_CONFIG_YAML.is_file():
        return {}
    try:
        import yaml
        data = yaml.safe_load(WECHAT_CONFIG_YAML.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _wechat_web_account() -> dict:
    """返回 web 端配置的公众号账号（accounts.web），无则空 dict。"""
    cfg = _wechat_load_yaml()
    accts = cfg.get("accounts") if isinstance(cfg.get("accounts"), dict) else {}
    acc = accts.get(WECHAT_WEB_ACCOUNT)
    return acc if isinstance(acc, dict) else {}


def _wechat_has_credentials() -> bool:
    acc = _wechat_web_account()
    return bool(acc.get("app_id") and acc.get("app_secret"))


def _wechat_save_credentials(app_id: str, app_secret: str, name: str = "", author: str = "") -> None:
    """把 AppID/AppSecret 写入 wechat-publisher.yaml 的 accounts.web（原子写，保留其它账号）。"""
    import yaml
    cfg = _wechat_load_yaml()
    if not isinstance(cfg.get("accounts"), dict):
        cfg["accounts"] = {}
    acc = cfg["accounts"].get(WECHAT_WEB_ACCOUNT)
    if not isinstance(acc, dict):
        acc = {}
    acc["name"] = name or acc.get("name") or "微信公众号"
    acc["app_id"] = app_id
    acc["app_secret"] = app_secret
    if author:
        acc["author"] = author
    acc.setdefault("author", "")
    cfg["accounts"][WECHAT_WEB_ACCOUNT] = acc
    # web 账号存在即设为默认，方便 CLI 直接用
    cfg.setdefault("default", WECHAT_WEB_ACCOUNT)
    WECHAT_CONFIG_YAML.parent.mkdir(parents=True, exist_ok=True)
    tmp = WECHAT_CONFIG_YAML.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, WECHAT_CONFIG_YAML)


def _wechat_clear_credentials() -> None:
    """删除 accounts.web 及其 token 缓存。"""
    import yaml
    cfg = _wechat_load_yaml()
    accts = cfg.get("accounts")
    if isinstance(accts, dict) and WECHAT_WEB_ACCOUNT in accts:
        accts.pop(WECHAT_WEB_ACCOUNT, None)
        if cfg.get("default") == WECHAT_WEB_ACCOUNT:
            cfg["default"] = next(iter(accts), "") if accts else ""
        try:
            tmp = WECHAT_CONFIG_YAML.with_suffix(".yaml.tmp")
            tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
            os.replace(tmp, WECHAT_CONFIG_YAML)
        except Exception:
            pass
    for cache in (WECHAT_SKILL_SCRIPTS / f".token_cache_{WECHAT_WEB_ACCOUNT}.json",
                  WECHAT_SKILL_SCRIPTS / ".token_cache.json"):
        try:
            cache.unlink()
        except OSError:
            pass


def _wechat_verify_token() -> tuple[bool, str]:
    """用当前 accounts.web 凭证调官方 token 接口验证。返回 (ok, message)。
    在子进程里跑，避免把 skill 的 import 副作用带进 web 进程。"""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from config import set_account\n"
        "from wechat_token import get_access_token\n"
        "set_account(%r)\n"
        "try:\n"
        "    t = get_access_token(force_refresh=True)\n"
        "    print('OK' if t else 'EMPTY')\n"
        "except Exception as e:\n"
        "    print('ERR:' + str(e))\n"
    ) % (str(WECHAT_SKILL_SCRIPTS), WECHAT_WEB_ACCOUNT)
    try:
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(WECHAT_SKILL_SCRIPTS),
                              env=_wechat_env(), capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "验证超时（网络或 IP 白名单问题）"
    out = (proc.stdout or "").strip().splitlines()
    last = out[-1] if out else ""
    if last == "OK":
        return True, ""
    if last.startswith("ERR:"):
        return False, last[4:].strip()[:200]
    err = (proc.stderr or "").strip().splitlines()[-1:] or ["验证失败"]
    return False, err[0][:200]


def _k(env, label, required=True, secret=True, aliases=None):
    return {"env": env, "label": label, "required": required, "secret": secret, "aliases": aliases or []}


def _model_spec(group: str, label: str | None = None) -> dict:
    spec = model_group(group)
    return {
        "label": label or spec["label"],
        "settings": spec.get("settings", []),
        "providers": spec["providers"],
    }


def _short_drama_spec() -> dict:
    """Image is required; video and cloud voice settings remain optional enhancements."""
    image = model_group("image")
    optional = []
    for group_name in ("video", "voice"):
        group = model_group(group_name)
        optional.extend({**key, "required": False} for key in group.get("settings", []))
        for provider in group["providers"]:
            optional.extend({**key, "required": False} for key in provider["keys"])
    seen = set()
    optional = [key for key in optional if not (key["env"] in seen or seen.add(key["env"]))]
    return {
        "label": "AI 短剧（生图必需 + 生视频/云配音可选）",
        "settings": [],
        "providers": [{
            "id": "drama",
            "name": "关键帧生图（必需）+ 视频生成与闭源配音（可选）",
            "keys": [*image["providers"][0]["keys"], *optional],
        }],
    }

SKILL_API_REQUIREMENTS: dict[str, dict] = {
    "ai-image-gen": _model_spec("image"),
    "ecom-details-image": _model_spec("image", "电商配图（AI 生图）"),
    "ai-video-gen": _model_spec("video"),
    "ai-music": _model_spec("music"),
    "voice-clone": _model_spec("voice", "声音克隆 / 云端 TTS"),
    # AI 短剧：编排 ai-image-gen(关键帧,必需) + ai-video-gen(生视频,可选,缺则退化图片短剧)。
    # 以生图为「已配置」基线（缺生图无法出关键帧）；生视频 key 同框可选填，也可在 ai-video-gen 卡片配。
    "short-drama": _short_drama_spec(),
    # 论文解读：MinerU 与生图均为可选（缺 MinerU 用 pdfplumber 兜底、缺生图用信息图/图表）。
    # 全 key 可选 → 不误报感叹号；但仍进注册表以便就地填 MINERU_API_TOKEN（无其它叶子 skill 承载它）。
    "paper-explainer": {
        "label": "论文解读（MinerU / 生图 均可选）",
        "providers": [
            {
                "id": "paper",
                "name": "MinerU 解析(可选, 缺则 pdfplumber) + 封面/概念生图(可选)",
                "keys": [
                    _k("MINERU_API_TOKEN", "MinerU API Token（可选，缺则用 pdfplumber 兜底）",
                       required=False),
                    _k("IMG_API_KEY", "生图 API Key（可选，用于封面/概念图）", required=False,
                       aliases=["OPENAI_API_KEY", "API_KEY"]),
                    _k("IMG_BASE_URL", "生图 API 根地址（可选）", required=False, secret=False,
                       aliases=["OPENAI_BASE_URL", "OPENAI_API_BASE", "BASE_URL"]),
                ],
            },
        ],
    },
}

_ENV_ALLOWLIST: set[str] = {
    "EASEL_DIRECT_API_BASE_URL", "EASEL_DIRECT_API_KEY", "EASEL_DIRECT_API_MODEL",
}
for _spec in SKILL_API_REQUIREMENTS.values():
    for _key in _spec.get("settings", []):
        _ENV_ALLOWLIST.add(_key["env"])
        _ENV_ALLOWLIST.update(_key.get("aliases", []))
    for _prov in _spec["providers"]:
        for _key in _prov["keys"]:
            _ENV_ALLOWLIST.add(_key["env"])
            _ENV_ALLOWLIST.update(_key.get("aliases", []))

ENV_FILE = PROJECT_ROOT / ".env"
_HARNESS_LOOP = None
_PLACEHOLDER_RE = re.compile(r"replace_me|your[-_]?api[-_]?key|xxx|^\.{3}$|^<.*>$", re.I)

TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts", ".html", ".htm", ".css", ".xml", ".yaml", ".yml", ".srt", ".vtt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """应用生命周期：关机时回收公众号扫码进程（替代已弃用的 on_event）。"""
    global _HARNESS_LOOP
    _HARNESS_LOOP = asyncio.get_running_loop()
    yield
    tasks = list(_HARNESS_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    _HARNESS_LOOP = None
    _stop_mp_login_on_shutdown()


app = FastAPI(title="Easel", docs_url=None, redoc_url=None, lifespan=_lifespan)

def _local_ports(env_port: str) -> set[str]:
    """本机写守卫放行的端口集合：固定的 7860/7870/5173，加上 `--port`/EASEL_PORT
    实际配置的端口——否则自定义端口跑 Easel 时，前端自己发的写请求（包括发消息
    的 /api/chat/stream）会被 local_write_guard 当跨站请求 403 掉。"""
    return {'7860', '7870', '5173', (env_port or '').strip() or '7860'}


_LOCAL_PORTS = _local_ports(os.environ.get('EASEL_PORT', ''))
_LOCAL_ORIGINS = [f'http://{host}:{port}'
                  for host in ('127.0.0.1', 'localhost') for port in _LOCAL_PORTS]
_LOCAL_ORIGINS += [o.strip() for o in os.environ.get('EASEL_EXTRA_ORIGINS', '').split(',') if o.strip()]
_EXTRA_HOSTS = [h.strip() for h in os.environ.get('EASEL_EXTRA_HOSTS', '').split(',') if h.strip()]

# VSCode/code-server 等远程开发环境常通过 VSCODE_PROXY_URI 这个反代域名访问 Easel
# （真实连接仍是本机回环，代理在同一台机器上转发）；不识别这个域名会让通过代理
# 打开的页面从第一个请求起就被下面的 TrustedHostMiddleware/CORS 拒绝——收紧前
# allow_origins=["*"] 时这条路是通的，别让这次收紧顺带堵死它。
def _proxy_uri_origin(uri: str) -> tuple[str, str] | None:
    """从 VSCODE_PROXY_URI 里取反代域名，返回 (host, origin)；解析不出 host 则 None。"""
    if not uri:
        return None
    parsed = urllib.parse.urlparse(uri)
    if not parsed.hostname:
        return None
    return parsed.hostname, f'{parsed.scheme}://{parsed.hostname}'


_proxy_origin = _proxy_uri_origin(os.environ.get('VSCODE_PROXY_URI', ''))
if _proxy_origin:
    _EXTRA_HOSTS.append(_proxy_origin[0])
    _LOCAL_ORIGINS.append(_proxy_origin[1])


def _loopback_peer(request: Request) -> bool:
    """对端是否来自回环地址。只有「没有 Origin 可判」时才用作判据。"""
    client = request.client
    if client is None:
        return False
    host = client.host or ''
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


app.add_middleware(CORSMiddleware, allow_origins=_LOCAL_ORIGINS, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1'] + _EXTRA_HOSTS)


@app.middleware('http')
async def local_write_guard(request: Request, call_next):
    """拦住「别的网站让浏览器替我改本机状态」的写请求，同时不误伤本机调用。

    两条判据，按请求实际形态二选一：
    ① 带 Origin（浏览器跨站发起）：Origin 必须在 _LOCAL_ORIGINS 里，否则 403。
    ② 不带 Origin（curl / 本机脚本 / 测试客户端）：要求对端来自回环地址。

    局限（重要）：这套防护假定 Origin/Host 头不可伪造——对浏览器成立，对裸
    HTTP 客户端不成立。本进程仍监听 0.0.0.0（见文件底部 uvicorn.run，未随
    此中间件收紧，兼容 Docker/远程容器端口转发场景），因此局域网内能直连到
    这个端口的攻击者，只要手写 curl 一起伪造 `Origin`/`Host` 头就能绕过本
    中间件与下面的 TrustedHostMiddleware。这里防的是「浏览器打开恶意网页」
    这一类跨站攻击，不是「网络可达即视为可信」的边界——不要把本服务暴露在
    不受信任的网络（公共 WiFi、无防火墙的公网主机）上。
    """
    origin = request.headers.get('origin')
    if request.url.path == '/api/clipper' and origin and re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
        headers = {
            'Access-Control-Allow-Origin': origin,
            'Access-Control-Allow-Methods': 'POST',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
            'Vary': 'Origin',
        }
        if request.method == 'OPTIONS':
            return JSONResponse({}, headers=headers)
        response = await call_next(request)
        response.headers.update(headers)
        return response
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        allowed = origin in _LOCAL_ORIGINS if origin else _loopback_peer(request)
        if not allowed:
            return JSONResponse({'detail': '仅允许从本机工作台操作。'}, status_code=403)
    return await call_next(request)


def list_personas() -> list[dict]:
    if not PROFILES_DIR.is_dir():
        return []
    result = []
    for d in sorted(PROFILES_DIR.iterdir()):
        if not d.is_dir() or not valid_persona_name(d.name):
            continue
        desc = ''
        identity = d / 'identity.md'
        if identity.is_file():
            for line in identity.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith('#') and not line.startswith('<!--'):
                    desc = line[:80]
                    break
        result.append({'name': d.name, 'description': desc})
    return result


def find_skill(name: str) -> str | None:
    """查找 SKILL，返回完整名或 None。与 CLI skill.py 一致。"""
    cands = [name, f'skill-{name}'] if not name.startswith('skill-') else [name]
    for cand in cands:
        if (SKILLS_DIR / 'builtin' / cand / 'SKILL.md').is_file():
            return cand
    return None


def _parse_skill_md(path: Path) -> tuple[str, str, str]:
    """解析 SKILL.md → (description, layer, body)。body 为去掉 frontmatter 的正文。
    正确处理 YAML 块标量 description（`>-` / `>` / `|` 后跟缩进多行）。"""
    text = path.read_text(encoding='utf-8')
    desc, layer, body = '', '', text
    lines = text.splitlines()
    if not (lines and lines[0].strip() == '---'):
        return desc, layer, body
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            end = i
            break
    if end is None:
        return desc, layer, body
    body = '\n'.join(lines[end + 1:]).strip()
    fm = lines[1:end]
    i = 0
    while i < len(fm):
        st = fm[i].strip()
        if st.startswith('layer:'):
            layer = st.split(':', 1)[1].strip().strip('"').strip("'")
            i += 1
        elif st.startswith('description:'):
            val = st.split(':', 1)[1].strip()
            if val and val[0] in '|>':
                block = []
                j = i + 1
                while j < len(fm):
                    if fm[j].strip() == '':
                        block.append('')
                        j += 1
                        continue
                    indent = len(fm[j]) - len(fm[j].lstrip())
                    if indent == 0:
                        break
                    block.append(fm[j].strip())
                    j += 1
                desc = ' '.join(x for x in block if x).strip()
                i = j
            else:
                desc = val.strip('"').strip("'")
                i += 1
        else:
            i += 1
    return desc, layer, body


def get_skills(persona: str | None = None) -> list[dict]:
    """技能库列表；按画像聚焦（profiles/skill_scopes.json，与知识库同源）：
    scope 空=全量；非空=仅命中（精确名/前缀/glob）。未配置该画像则全量。"""
    env = _read_env()
    patterns = _persona_skill_scope(persona)
    result = []
    sd = SKILLS_DIR / 'builtin'
    if sd.is_dir():
        for d in sorted(sd.iterdir()):
            if d.is_dir() and (d / 'SKILL.md').is_file():
                if not _skill_in_scope(d.name, patterns):
                    continue
                desc, layer, _ = _parse_skill_md(d / 'SKILL.md')
                needs_api = d.name in SKILL_API_REQUIREMENTS
                result.append({
                    'name': d.name,
                    'description': desc,
                    'layer': layer,
                    'needsApi': needs_api,
                    'apiConfigured': _skill_api_configured(d.name, env) if needs_api else True,
                })
    return result


def clean_agent_output(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        c = re.sub(r'\x1b\[[0-9;]*m', '', line)
        if c.startswith('[') and any(t in c[:40] for t in ('[provider-', '[agents/', '[agent/', '[plugins]', '[tools]', '[diagnostic]', '[fetch-', '[heartbeat]', '[health-', '[gateway]')):
            continue
        if c.strip():
            lines.append(c)
    return '\n'.join(lines).strip()


def _proxy_env() -> dict[str, str]:
    """返回带外网代理的环境变量（保护内网直连）。"""
    env = os.environ.copy()
    env.setdefault('EASEL_ROOT', str(PROJECT_ROOT))
    env.setdefault('http_proxy', os.environ.get('EASEL_PROXY', ''))
    env.setdefault('https_proxy', os.environ.get('EASEL_PROXY', ''))
    env.setdefault('no_proxy', 'localhost,127.0.0.1,10.*,*.xiaohongshu.com,*.devops.xiaohongshu.com,*.douyin.com,*.kuaishou.com,*.zhihu.com,*.bilibili.com,*.weixin.qq.com,*.qq.com')
    return env


def _publish_env() -> dict[str, str]:
    """发布子进程 env：在 _proxy_env 基础上禁用脚本侧日历自动记录——
    发布页由 web 自己回流 _schedule.json，脚本再记一次会重复。对话页 Agent 直跑
    脚本时不经过这里，flag 未设 → 脚本自动记录（见 calendar_ops.record_publish）。"""
    env = _proxy_env()
    env['EASEL_CALENDAR_AUTORECORD'] = '0'
    return env


def _wechat_env() -> dict[str, str]:
    """公众号 API 子进程 env，决定微信 API 从哪个 IP 出网（公众号白名单要求固定出口 IP）。

    两种模式：
    - 设了 WECHAT_EGRESS_PROXY（如反向隧道到你本机/固定 IP 中转）→ 让微信 API **走这个代理**出网，
      微信看到的是该代理的公网 IP，白名单加它即可。其它外网仍走公司代理。
    - 未设 → 微信 API 走**直连**（api.weixin.qq.com 进 no_proxy）。注意本机直连出口也是共享 NAT
      轮询池（见 WECHAT_OA_INTEGRATION.md §4），直连仅在出口 IP 恰好固定的机器上可靠。"""
    env = _publish_env()
    wx_hosts = ("api.weixin.qq.com", "mp.weixin.qq.com")
    egress = os.environ.get("WECHAT_EGRESS_PROXY", "").strip()
    if egress:
        # 微信 API 走指定固定出口代理；从 no_proxy 里去掉微信域名，确保不被旁路成直连。
        env["http_proxy"] = env["https_proxy"] = egress
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = egress
        kept = [h for h in env.get("no_proxy", "").split(",") if h and not any(w in h for w in wx_hosts)]
        env["no_proxy"] = ",".join(kept)
        env["NO_PROXY"] = env["no_proxy"]
    else:
        existing = env.get("no_proxy", "")
        env["no_proxy"] = (existing + "," + ",".join(wx_hosts)) if existing else ",".join(wx_hosts)
        env["NO_PROXY"] = env["no_proxy"]
    return env


def _persona_prefix(persona: str | None) -> str:
    """把画像作为消息前缀内联（复用 easel.persona，与 CLI/skill 同源）。"""
    return persona_prefix(persona)


def _read_env() -> dict[str, str]:
    """宽松解析项目根 .env → {KEY: value}。跳过注释与非 KEY=value 行（容忍多行值残行）。"""
    result = {}
    if not ENV_FILE.is_file():
        return result
    for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        key, val = s.split('=', 1)
        key = key.strip()
        if key.isidentifier() or key.replace('-', '_').isidentifier():
            result[key] = val.strip()
    return result


def _is_set(val: str | None) -> bool:
    """非空且非占位符才算真正配置了。"""
    if not val or not val.strip():
        return False
    return not _PLACEHOLDER_RE.search(val.strip())


def _mask(val: str) -> str:
    """脱敏：只留尾 4 位（短值全遮）。"""
    v = val.strip()
    if len(v) <= 4:
        return '••••'
    return '••••' + v[-4:]


# 要从回显文本里抹掉的 .env 键：命中这些词的一律当机密处理
_SECRETISH_RE = re.compile(r'KEY|TOKEN|SECRET|PASSWORD|COOKIE', re.I)


def _scrub_secrets(text: str) -> str:
    """把 .env 里的机密值从要回显给前端的文本里抹掉。

    子进程 stderr 会被原样带到浏览器，而 agent 底座的报错可能回显配置值；
    按「已知机密的字面量」替换最稳妥，不依赖猜正则能否匹配各家 key 的格式。
    """
    out = text
    for key, val in _read_env().items():
        v = val.strip().strip('"').strip("'")
        # 太短的值（占位符、枚举值如 none/http）替换会误伤正常文本
        if len(v) >= 8 and _SECRETISH_RE.search(key):
            out = out.replace(v, _mask(v))
    return out


def _key_configured(key: dict, env: dict[str, str]) -> bool:
    '某个 key（含别名）是否已配置。'
    if _is_set(env.get(key['env'])):
        return True
    return any(_is_set(env.get(a)) for a in key.get('aliases', []))


def _skill_api_configured(skill: str, env: dict[str, str] | None = None) -> bool:
    'SKILL 是否已具备可用配置：任一 provider 的全部 required key 齐全。'
    spec = SKILL_API_REQUIREMENTS.get(skill)
    if not spec:
        return True
    env = _read_env() if env is None else env
    for prov in spec['providers']:
        if all(_key_configured(k, env) for k in prov['keys'] if k['required']):
            return True
    return False


# .env 的值是裸写 `KEY=value` 的，而 setup.sh:310/450 会 `source .env`。bash 在赋值右侧
# 不做分词和通配，但**照做**命令替换，且元字符会截断赋值另起一条命令 —— 所以 `$(…)`、反引号、
# `;`/`&`/`|`/`(`/`)`/`<`/`>`/空白 都等于任意命令执行。加引号写入不行：scripts/gateway.sh:63
# 用 `sed -n 's/^KEY=//p'` 裸取值，引号会跟着漏进端口号。只能在入口把值的字符集收死。
# 这里能写的全是 API Key / Base URL / 模型 id / 端口，没有一个需要空格或上面那些字符。
_ENV_VALUE_OK = re.compile(r'[A-Za-z0-9_.:/@+\-=~,%?#\[\]]*')


def _guard_env_values(updates: dict[str, str]) -> None:
    """集中拦截 .env 值注入：值里带换行能往 .env 追加任意行，带 `$(…)` 能直接执行命令
    —— 两者都等于任何能调到写 .env 接口的人都能拿到命令执行。键名同理。两个写入口都必须先过这道。"""
    for k, v in updates.items():
        if any(c in (k or '') for c in '\r\n=') or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k or ''):
            raise HTTPException(400, f'非法的配置键名：{(k or "")[:40]!r}')
        v = v or ''
        if any(c in v for c in '\r\n\x00'):
            raise HTTPException(400, f'配置值不能包含换行符：{k}')
        if not _ENV_VALUE_OK.fullmatch(v):
            bad = ''.join(sorted({c for c in v if not _ENV_VALUE_OK.fullmatch(c)}))
            raise HTTPException(400, f'配置值含不允许的字符（{bad!r}）：{k}')


def _is_local_gateway_base(url: str) -> bool:
    """这个供应商的 baseUrl 是否是本机回环代理地址（换址校验时不作为「换址」判据）。

    本地代理地址与真实上游天生不相等：面板显示/回传的是上游地址，而这里存的是回环地址。
    凡是拿「baseUrl 变了」当判据的逻辑都得先问过这里，否则每次保存都会被误判成「换了地址」。
    """
    return str(url or '').startswith('http://127.0.0.1:8890')


# 设置面板里「改了 Base URL 就必须重填 Key」要比对的 .env 键位（槽位 → (base 键, key 键)）。
_SLOT_ENV_KEYS = {
    'direct-api': ('EASEL_DIRECT_API_BASE_URL', 'EASEL_DIRECT_API_KEY'),
    'openai': ('OPENAI_BASE_URL', 'OPENAI_API_KEY'),
    'relay': ('EASEL_LLM_BASE_URL', 'EASEL_LLM_API_KEY'),
    'anthropic': ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY'),
    'siliconflow': ('SILICONFLOW_BASE_URL', 'SILICONFLOW_API_KEY'),
}


def _valid_base_url(u: str) -> bool:
    '整串校验（不是只看开头）：必须是 http(s)://host，且不带控制字符与 URL 内嵌凭据。'
    if any(c in u for c in '\r\n\t\x00') or '@' in u:
        return False
    try:
        p = urllib.parse.urlparse(u)
    except ValueError:
        return False
    return p.scheme in ('http', 'https') and bool(p.hostname)


def _ssrf_safe(u: str) -> bool:
    """自测会带着真 Key 去打这个地址，所以不许指向本机/内网/云元数据——
    这些地址上的服务通常无鉴权，一旦被当成「模型端点」就成了打内网的跳板。"""
    try:
        host = urllib.parse.urlparse(u).hostname or ''
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001  解析不了就当不安全
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _direct_api_enabled() -> bool:
    return _harness_settings()["runtime"] == "api"


def _harness_settings() -> dict:
    try:
        return harness_store.settings(OUTPUTS_DIR, read_settings(ENV_FILE))
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error


def _chat_backend(session_id: str | None, persona: str | None = None) -> str:
    """会话底座探测：按画像级隔离的 effective session key（_eff_sk）查绑定/历史，
    使同一 plain sessionId 的不同画像互不串味；legacy 已有历史的 plain id 保持原名。"""
    selected = "api" if _direct_api_enabled() else _harness_settings()["runtime"]
    if not session_id:
        return selected
    sk = _eff_sk(session_id, persona)
    try:
        record = harness_store.binding(SESSIONS_DIR, sk)
    except HarnessError as error:
        raise HTTPException(409, str(error)) from error
    owner = record.get("runtime")
    if not owner:
        if history_path(SESSIONS_DIR, sk).is_file():
            owner = "api"
        elif _turn_file(f"web:{sk}").is_file():
            owner = selected
    if owner and owner != selected:
        raise HTTPException(409, f"此会话属于 {owner}，当前选择 {selected}；请新建会话，原历史不变")
    return selected


def _model_target_allowed(base: str, slot: str = "") -> bool:
    # Trust only the exact gateway configured by the user, not arbitrary private URLs.
    settings = read_settings(ENV_FILE)
    configured = settings.get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/")
    if slot == "direct-api" and _direct_api_enabled() and base.rstrip("/") == configured:
        try:
            api_config(settings, require_model=False)
            return True
        except DirectAPIError:
            return False
    return _ssrf_safe(base)


def _write_env(updates: dict[str, str]) -> None:
    '就地更新命中的 KEY、其余行原样保留，未命中的追加末尾；空串则删除该行。原子写。'
    updates = {k: v for k, v in updates.items() if k in _ENV_ALLOWLIST}
    _guard_env_values(updates)
    if not updates:
        return
    lines = ENV_FILE.read_text(encoding='utf-8').splitlines() if ENV_FILE.is_file() else []
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        matched = None
        if s and not s.startswith('#') and '=' in s:
            k = s.split('=', 1)[0].strip()
            if k in updates:
                matched = k
        if matched is not None:
            seen.add(matched)
            val = updates[matched]
            if val.strip() == '':
                continue
            out.append(f'{matched}={val}')
            continue
        out.append(line)
    appended = [f'{k}={v}' for k, v in updates.items() if k not in seen and v.strip() != '']
    if appended:
        if out and out[-1].strip() != '':
            out.append('')
        out.append('# ---- Easel API keys (added via Web) ----')
        out.extend(appended)
    tmp = ENV_FILE.with_suffix('.env.tmp')
    tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
    tmp.replace(ENV_FILE)


def _api_spec_status(skill: str, env: dict[str, str]) -> dict:
    '返回注册表项 + 每个 key 当前配置状态与脱敏值（不回传明文）。'
    spec = SKILL_API_REQUIREMENTS[skill]

    def key_status(k: dict) -> dict:
        raw = env.get(k['env'], '')
        return {
            'env': k['env'],
            'label': k['label'],
            'required': k['required'],
            'secret': k['secret'],
            'choices': list(k.get('choices', [])),
            'configured': _key_configured(k, env),
            'masked': _mask(raw) if k['secret'] and _is_set(raw) else (raw if not k['secret'] else ''),
        }

    providers = []
    for prov in spec['providers']:
        keys = [key_status(k) for k in prov['keys']]
        providers.append({'id': prov['id'], 'name': prov['name'], 'keys': keys})
    return {
        'label': spec['label'],
        'settings': [key_status(k) for k in spec.get('settings', [])],
        'providers': providers,
    }


def _publishing_message(message: str) -> str:
    if publishing_mode(read_settings(ENV_FILE)) == "manual":
        return ("【当前 Web 发布方式：人工提交】AI 只负责创作、排版、素材和审核建议。"
                "不要调用发布脚本、发布接口、浏览器代填或代点发布。"
                "引导用户在发布中心审核、复制或导出，再在官方页面亲自提交。"
                "导出、复制、打开网页和排期均不代表已发布。\n\n" + message)
    return message


def run_agent_sync(msg: str, timeout: int = TIMEOUT_DIRECT, session_id: str | None = None) -> str:
    if _direct_api_enabled():
        raise HTTPException(409, "API 直连模式支持聊天；此操作需要具备文件和技能执行工具的 Agent 运行时")
    msg = _publishing_message(msg)
    sk = session_id or f'web-{int(time.time() * 1000)}'
    runtime = _chat_backend(session_id)
    # 非 api 运行（opencode/codex/acp/pi）走 harness 适配层；api 直连在上面已 409 拒绝。
    async def execute():
        request = ChatRequest(message=msg, sessionId=sk)
        errors = []
        def emit(kind, text):
            if kind == "error":
                errors.append(text)
        result = await _run_harness_chat(request, sk, msg, runtime, _harness_settings(), emit,
                                         timeout, interactive=False)
        if errors:
            raise HTTPException(502, errors[0])
        return result
    if _HARNESS_LOOP is not None and _HARNESS_LOOP.is_running():
        return asyncio.run_coroutine_threadsafe(execute(), _HARNESS_LOOP).result(timeout + 15)
    return asyncio.run(execute())


def _file_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTS:
        return 'image'
    if ext in VIDEO_EXTS:
        return 'video'
    if ext in AUDIO_EXTS:
        return 'audio'
    if ext in TEXT_EXTS:
        return 'text'
    return 'binary'


def _file_meta(f: Path, rel: str) -> dict:
    try:
        st = f.stat()
        mtime, size = int(st.st_mtime), st.st_size
    except OSError:
        mtime, size = 0, 0
    return {'name': f.name, 'path': rel, 'kind': _file_kind(f.name), 'mtime': mtime, 'size': size}


def _build_output_node(path: Path, rel: str) -> dict:
    """递归构建产物树节点：文件→file 节点；目录→dir 节点带 children + 递归 fileCount。"""
    if path.is_dir():
        children = []
        for c in sorted(path.iterdir()):
            if c.name.startswith('.'):   # 嵌套层只跳隐藏文件；_base.mp4 等以 _ 开头的产物要保留
                continue
            children.append(_build_output_node(c, f'{rel}/{c.name}'))
        mtime = max((x['mtime'] for x in children), default=int(path.stat().st_mtime))
        file_count = sum(x.get('fileCount', 1) if x['type'] == 'dir' else 1 for x in children)
        return {'name': path.name, 'type': 'dir', 'path': rel, 'mtime': mtime,
                'children': children, 'fileCount': file_count}
    m = _file_meta(path, rel)
    m['type'] = 'file'
    return m


def _read_project_meta(proj: Path) -> dict:
    """读项目目录的 .easel.json 展示头，附封面/成品的解析路径供前端富展示。

    只取展示相关字段（不含编排 steps）。cover 解析优先级：
    展示头声明的 cover → 首个成品媒体 → 目录内首张图/视频（兜底）。
    """
    mf = proj / ".easel.json"
    if not mf.is_file():
        return {}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return {}
    meta = {k: data.get(k) for k in
            ("title", "summary", "platform", "kind", "status", "tags", "deliverables")
            if data.get(k) not in (None, "", [])}
    if not meta:
        return {}

    def _rel_if_exists(name: str) -> str:
        return f"{proj.name}/{name}" if name and (proj / name).is_file() else ""

    # 封面解析
    cover_rel = ""
    declared = data.get("cover")
    if declared and (proj / declared).is_file():
        cover_rel = f"{proj.name}/{declared}"
    if not cover_rel:
        for d in (data.get("deliverables") or []):
            if _file_kind(d) in ("image", "video") and (proj / d).is_file():
                cover_rel = f"{proj.name}/{d}"
                break
    if cover_rel:
        meta["cover"] = cover_rel
    # 成品路径（前端「成品区」高亮用）：解析为 outputs 相对路径，只留真实存在的
    meta["deliverablePaths"] = [f"{proj.name}/{d}" for d in (data.get("deliverables") or [])
                                if (proj / d).is_file()]
    return meta


# ── 外部只读内容源（如本地知识库）：并入内容库，但懒加载、只读、不可删除 ──
# 配置来自 .env 的 EASEL_CONTENT_SOURCES，格式 name=path（; 分隔多个），
# 或 JSON 数组 [{"name":..., "path":...}]；相对路径按项目根解析。
_CONTENT_SRC_RE = re.compile(r'^[^\x00-\x1f/\\]+$')


def _content_sources() -> dict[str, Path]:
    """{source_name: 绝对根目录}。外部内容源（本地知识库等），只读并入内容库。"""
    raw = read_settings(ENV_FILE).get("EASEL_CONTENT_SOURCES", "").strip()
    if not raw:
        return {}
    entries: list[tuple[str, str]] = []
    if raw.lstrip().startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("name") and item.get("path"):
                        entries.append((str(item["name"]).strip(), str(item["path"]).strip()))
        except Exception:
            pass
    else:
        for part in re.split(r"[;,\n]", raw):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                name, _, path = part.partition("=")
                entries.append((name.strip(), path.strip()))
            else:
                entries.append((Path(part).name, part))
    sources: dict[str, Path] = {}
    for name, path in entries:
        if not name or not path or not _CONTENT_SRC_RE.match(name):
            continue
        root = Path(path).expanduser()
        if not root.is_absolute():
            root = (PROJECT_ROOT / path).resolve()
        if root.is_dir():
            sources[name] = root
    return sources


def _resolve_external(rel: str) -> tuple[Path, Path] | None:
    """把 @<sourceName>/<子路径> 解析到对应外部源根内（防穿越）。
    非外部（不以 @ 开头、source 未配置或名称非法）返回 None，交由 outputs/ 逻辑处理。"""
    if not rel or not rel.startswith("@"):
        return None
    rest = rel[1:]
    name, sep, sub = rest.partition("/")
    if not _CONTENT_SRC_RE.match(name):
        return None
    root = _content_sources().get(name)
    if root is None:
        return None
    if not sep:
        return (root, root)
    full = (root / sub).resolve()
    rroot = root.resolve()
    if rroot != full and rroot not in full.parents:
        raise HTTPException(403, "非法路径")
    return (root, full)


def _external_rel(path: str) -> str:
    """@name/sub 形式路径去掉源名前缀，返回相对源根的相对路径（源根本身返回 ''）。"""
    if not path or not path.startswith("@"):
        return ""
    rest = path[1:]
    name, sep, sub = rest.partition("/")
    return sub if sep else ""


def _dir_mtime(d: Path) -> int:
    try:
        return int(d.stat().st_mtime)
    except OSError:
        return 0


def _count_files(d: Path, cap: int = 100000) -> int:
    """统计目录内文件数（跳过隐藏项）；超大目录封顶，避免扫描失控。"""
    n = 0
    try:
        for c in d.iterdir():
            if c.name.startswith("."):
                continue
            if c.is_dir():
                n += _count_files(c, cap)
            else:
                n += 1
            if n >= cap:
                break
    except OSError:
        pass
    return n


def _build_external_source_node(name: str, root: Path, patterns: list[str] | None = None) -> dict:
    """外部内容源（本地知识库）顶层节点：只展开第一层（源根 → 目录/散文件）。
    更深层走 /api/output-children 懒加载（知识库可能上万文件，不全量入树）。
    external 标记：前端隐藏删除、后端拒绝写/删（只读）。
    patterns 为当前画像对该源的聚焦 glob（None/空=通用，全量）；只保留命中及其祖先。"""
    children = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        entries = []
    for c in entries:
        if c.name.startswith("."):
            continue
        if patterns and not _kb_in_scope(c.name, patterns):
            continue
        if c.is_dir():
            children.append({
                "name": c.name, "type": "dir", "path": f"@{name}/{c.name}",
                "external": True, "mtime": _dir_mtime(c), "fileCount": _count_files(c),
            })
        else:
            fm = _file_meta(c, f"@{name}/{c.name}")
            fm["type"] = "file"
            fm["external"] = True
            children.append(fm)
    mt = max([_dir_mtime(root)] + [x.get("mtime", 0) for x in children], default=0)
    fc = sum(x.get("fileCount", 1) if x["type"] == "dir" else 1 for x in children)
    return {
        "name": name, "type": "dir", "path": f"@{name}", "external": True,
        "children": children, "mtime": mt, "fileCount": fc,
    }


def get_output_tree(persona: str | None = None) -> list[dict]:
    items = []
    kb_patterns = _persona_kb_scope(persona)
    if OUTPUTS_DIR.is_dir():
        for e in sorted(OUTPUTS_DIR.iterdir()):
            # 内容库只展示「项目目录」：跳过系统目录(_login/_publish/...)、隐藏项、
            # 以及根目录散文件（按新规约产物必在项目目录内，根散文件=系统状态/残渣）。
            if e.name.startswith('.') or e.name.startswith('_'):
                continue
            if not e.is_dir() or e.name in SYSTEM_TOPLEVEL_DIRS:
                continue
            node = _build_output_node(e, e.name)
            meta = _read_project_meta(e)
            if meta:
                node['meta'] = meta
            items.append(node)
    # 外部只读内容源（本地知识库）：作为顶层节点并入内容库，懒加载、只读、不可删除。
    # 按当前画像聚焦（kb_scopes.json）过滤其顶层子节点：查询结果随画像而不同。
    for name, root in _content_sources().items():
        try:
            items.append(_build_external_source_node(name, root, kb_patterns))
        except Exception:
            # 一个外部源出错（路径不可读等）不影响其余内容库展示。
            continue
    # 按最后修改时间倒序：最近产物排最前（供工作台「最近产物」与内容库时间排序）
    return sorted(items, key=lambda x: x.get('mtime', 0), reverse=True)


def _safe_output_path(rel: str) -> Path:
    '把相对路径解析到 outputs/ 或已配置的外部内容源内（防路径穿越）。'
    ext = _resolve_external(rel)
    if ext is not None:
        full = ext[1]
        if not full.is_file():
            raise HTTPException(404, '文件不存在')
        return full
    full = (OUTPUTS_DIR / rel).resolve()
    root = OUTPUTS_DIR.resolve()
    if root != full and root not in full.parents:
        raise HTTPException(403, '非法路径')
    if not full.is_file():
        raise HTTPException(404, '文件不存在')
    return full


@app.get("/")
async def index():
    no_cache = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
    react_index = REACT_DIR / "index.html"
    if react_index.is_file():
        return FileResponse(react_index, media_type="text/html", headers=no_cache)
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers=no_cache)


@app.get("/onepage")
async def onepage():
    return FileResponse(STATIC_DIR / "onepage.html", media_type="text/html")


@app.get("/assets/{path:path}")
async def react_assets(path: str):
    base = (REACT_DIR / "assets").resolve()
    fp = (REACT_DIR / "assets" / path).resolve()
    if base != fp and base not in fp.parents:
        raise HTTPException(403, "非法路径")
    if not fp.is_file():
        raise HTTPException(404)
    return FileResponse(fp, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/static/{path:path}")
async def static_file(path: str):
    base = STATIC_DIR.resolve()
    fp = (STATIC_DIR / path).resolve()
    if base != fp and base not in fp.parents:
        raise HTTPException(403, "非法路径")
    if not fp.is_file():
        raise HTTPException(404)
    # HTML entrypoints must not be cached: the intro page is edited in-place during local development.
    headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"} if fp.suffix.lower() in {".html", ".htm"} else {}
    return FileResponse(fp, headers=headers)


@app.get("/api/status")
async def api_status():
    direct = _direct_api_enabled()
    runtime = _harness_settings()["runtime"]
    ready = False
    if direct:
        try:
            api_config(read_settings(ENV_FILE))
            ready = True
        except DirectAPIError:
            pass
    elif runtime == "api":
        ready = True
    else:
        ready = harnesses.describe(runtime)["installed"]
    return {"gateway": ready, "transport": "api" if direct else runtime, "runtime": runtime,
            "skills": get_skills(), "personas": list_personas()}


@app.get("/api/settings/bootstrap")
async def api_settings_bootstrap():
    """首次打开时的自检：模型配好了吗、上次安装留了什么没做完。

    安装脚本不再在终端问 API Key（改为引导到这里配），所以前端需要一个可靠的
    「还缺什么」判据。api 直连模式下 modelConfigured 要求 .env / easel-models.yaml
    里有可用的认证与主模型；harness 运行（opencode/codex/acp/pi）则以 CLI 是否安装为准。
    """
    direct = _direct_api_enabled()
    runtime = _harness_settings()["runtime"]
    if runtime != "api":
        installed = harnesses.describe(runtime)["installed"]
        return {"modelConfigured": installed, "envKeyOk": installed, "routeOk": installed,
                "routeDetail": "CLI 已安装；请在终端完成原生登录，并发送消息验证模型可用性",
                "gatewayUp": installed, "installWarnings": [],
                "runtime": runtime, "authVerified": False}
    env_ok = False
    route_ok = False
    route_detail = ''
    try:
        api_config(read_settings(ENV_FILE))
        env_ok = route_ok = True
    except DirectAPIError as error:
        route_detail = str(error)
    except Exception as e:  # noqa: BLE001
        route_detail = f'自检失败：{e}'

    warnings: list[dict] = []
    try:
        rep = PROJECT_ROOT / 'outputs' / '_install' / 'last-install.json'
        if rep.is_file():
            warnings = json.loads(rep.read_text(encoding='utf-8')).get('warnings') or []
    except Exception:  # noqa: BLE001
        warnings = []

    return {
        'modelConfigured': bool(env_ok and route_ok),
        'envKeyOk': env_ok,
        'routeOk': bool(route_ok),
        'routeDetail': route_detail,
        'gatewayUp': bool(route_ok),
        'installWarnings': warnings,
    }


@app.get("/api/personas")
async def api_personas():
    return list_personas()


@app.get("/api/persona/{name}")
async def api_persona(name: str):
    text = load_profile_text(name)
    if not text:
        raise HTTPException(404, "画像不存在")
    return {"name": name, "content": text}


def _valid_persona_name(name: str) -> bool:
    return valid_persona_name(name)


def _persona_file_path(name: str, filename: str) -> Path:
    """校验画像名/文件名，返回 profiles/<name>/<filename> 的安全路径。"""
    if not _valid_persona_name(name):
        raise HTTPException(400, "画像名非法")
    if not filename.endswith(".md") or "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(400, "文件名非法")
    pd = (PROFILES_DIR / name).resolve()
    fp = (pd / filename).resolve()
    if pd != fp.parent or PROFILES_DIR.resolve() not in pd.parents:
        raise HTTPException(403, "非法路径")
    return fp


@app.get("/api/persona/{name}/files")
async def api_persona_files(name: str):
    """返回画像六维文件原文（按固定顺序 + 其余 .md），供在线编辑。"""
    if not profile_exists(name):
        raise HTTPException(404, "画像不存在")
    pd = PROFILES_DIR / name
    ordered = list(_FILE_ORDER) + sorted(f.name for f in pd.glob("*.md") if f.name not in _FILE_ORDER)
    files = []
    for fn in ordered:
        fp = pd / fn
        files.append({"filename": fn, "content": fp.read_text(encoding="utf-8") if fp.is_file() else ""})
    return {"name": name, "files": files}


class PersonaFileRequest(BaseModel):
    filename: str
    content: str


@app.put("/api/persona/{name}/file")
async def api_persona_file_save(name: str, req: PersonaFileRequest):
    """保存画像单个维度文件（原子写）。"""
    if not profile_exists(name):
        raise HTTPException(404, "画像不存在")
    fp = _persona_file_path(name, req.filename)
    tmp = fp.with_suffix(".md.tmp")
    tmp.write_text(req.content, encoding="utf-8")
    tmp.replace(fp)
    return {"ok": True, "filename": req.filename}


@app.delete("/api/persona/{name}")
async def api_persona_delete(name: str):
    """删除整个画像目录。"""
    if not _valid_persona_name(name):
        raise HTTPException(400, "画像名非法")
    pd = (PROFILES_DIR / name).resolve()
    if PROFILES_DIR.resolve() not in pd.parents or not pd.is_dir():
        raise HTTPException(404, "画像不存在")
    import shutil
    shutil.rmtree(pd)
    return {"ok": True, "deleted": name}


@app.get("/api/skills")
async def api_skills(persona: str | None = None):
    return get_skills(persona)


@app.get("/api/skill/{name}")
async def api_skill_detail(name: str):
    """单个 SKILL 详情：描述 + 正文 + API 需求与当前配置状态（脱敏）。"""
    full = find_skill(name)
    if full is None:
        raise HTTPException(404, f"SKILL '{name}' 不存在")
    desc, layer, body = _parse_skill_md(SKILLS_DIR / "builtin" / full / "SKILL.md")
    needs_api = full in SKILL_API_REQUIREMENTS
    env = _read_env()
    return {
        "name": full,
        "layer": layer,
        "description": desc,
        "body": body,
        "needsApi": needs_api,
        "apiConfigured": _skill_api_configured(full, env) if needs_api else True,
        "apiSpec": _api_spec_status(full, env) if needs_api else None,
    }


class EnvUpdateRequest(BaseModel):
    updates: dict[str, str]


@app.post("/api/env")
async def api_env_save(req: EnvUpdateRequest):
    """写 API key 到项目根 .env（仅允许注册表内 env 名）。返回更新后各 skill 的配置状态。"""
    bad = [k for k in (req.updates or {}) if k not in _ENV_ALLOWLIST]
    if bad:
        raise HTTPException(400, f"不允许写入的变量：{', '.join(bad)}")
    _write_env(req.updates or {})
    env = _read_env()
    return {
        "ok": True,
        "skills": {s: _skill_api_configured(s, env) for s in SKILL_API_REQUIREMENTS},
    }


# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 环境安装（skills/shared/scripts/install_tool.py 引擎桥）
# 体检走引擎 check --json；安装后台跑、前端轮询 /api/env/job/{id}。
# ═══════════════════════════════════════════════════════════════════════

INSTALL_TOOL = SHARED_SCRIPTS / "install_tool.py"
_ENV_TOOLS_CACHE: dict = {"ts": 0.0, "data": None}
_ENV_JOBS: dict[str, dict] = {}


@app.get("/api/env/tools")
async def api_env_tools(refresh: bool = False):
    """环境体检：引擎 check --json（15 秒缓存；refresh=1 强制重测）。"""
    if not refresh and _ENV_TOOLS_CACHE["data"] and time.time() - _ENV_TOOLS_CACHE["ts"] < 15:
        return _ENV_TOOLS_CACHE["data"]
    try:
        proc = await asyncio.to_thread(lambda: subprocess.run(
            [sys.executable, str(INSTALL_TOOL), "--json", "check"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=240, cwd=str(PROJECT_ROOT)))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "环境体检超时，请稍后再试")
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise HTTPException(500, f"环境体检失败：{(proc.stderr or '').strip()[-300:] or '无输出'}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"体检输出解析失败：{e}")
    data["cachedAt"] = int(time.time())
    _ENV_TOOLS_CACHE.update({"ts": time.time(), "data": data})
    return data


class EnvInstallRequest(BaseModel):
    id: str


_INSTALL_IDS_CACHE: dict = {"ts": 0.0, "ids": frozenset()}


def _install_tool_ids() -> frozenset[str]:
    """引擎里真实存在的配方 id（缓存 5 分钟）。安装接口只认这张表里的 id——
    只按正则放行的话，`--help` 这类带横线的串会被 argparse 当选项吃掉。"""
    if _INSTALL_IDS_CACHE["ids"] and time.time() - _INSTALL_IDS_CACHE["ts"] < 300:
        return _INSTALL_IDS_CACHE["ids"]
    try:
        p = subprocess.run([sys.executable, str(INSTALL_TOOL), "--json", "list"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, cwd=str(PROJECT_ROOT))
        ids = frozenset(t["id"] for t in (json.loads(p.stdout).get("tools") or []) if t.get("id"))
    except Exception:  # noqa: BLE001
        return _INSTALL_IDS_CACHE["ids"]
    if ids:
        _INSTALL_IDS_CACHE.update({"ts": time.time(), "ids": ids})
    return ids


@app.post("/api/env/install")
async def api_env_install(req: EnvInstallRequest):
    """后台安装（引擎 install）：立即返回 jobId，前端轮询进度。"""
    tid = (req.id or "").strip()
    known = await asyncio.to_thread(_install_tool_ids)
    if tid not in known:
        raise HTTPException(400, f"无效的工具 id：{tid[:40]!r}")
    job_id = uuid.uuid4().hex[:16]
    if len(_ENV_JOBS) >= 200:                   # 任务表不能无限长：清掉最老的已结束任务
        for k, _ in sorted((kv for kv in _ENV_JOBS.items() if kv[1].get("ended")),
                           key=lambda kv: kv[1]["ended"])[:100]:
            _ENV_JOBS.pop(k, None)
    job = {"jobId": job_id, "id": tid, "state": "running", "lines": [],
           "result": None, "started": int(time.time()), "ended": None}
    _ENV_JOBS[job_id] = job

    def _run() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                [sys.executable, str(INSTALL_TOOL), "--json", "install", tid],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT))
            # stdout 必须**并发**抽干：串行地先读完 stderr 再读 stdout，子进程一旦往 stdout
            # 写满管道缓冲（64K）就会阻塞，而我们还堵在 stderr 上——双向死锁。
            _out: list[str] = []
            t_out = threading.Thread(target=lambda: _out.append(proc.stdout.read() or ""), daemon=True)
            t_out.start()
            for line in proc.stderr:            # 引擎进度日志走 stderr
                line = line.rstrip()
                if line:
                    job["lines"] = (job["lines"] + [line])[-40:]   # 只留最近 40 行（换列表，避免读端正在序列化时被就地改）
            try:
                proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:   # 卡死的安装进程必须真的杀掉，否则线程与子进程永久泄漏
                proc.kill()
                proc.wait(timeout=30)
                raise
            t_out.join(timeout=30)
            out = ("".join(_out)).strip()
            if out:
                job["result"] = (json.loads(out).get("results") or [None])[0]
            job["state"] = "ok" if (job["result"] or {}).get("state") == "ok" else "fail"
        except Exception as e:  # noqa: BLE001
            job["state"] = "fail"
            job["result"] = {"id": tid, "state": "fail", "detail": f"{type(e).__name__}: {e}"}
        finally:
            job["ended"] = int(time.time())
            _ENV_TOOLS_CACHE["ts"] = 0.0        # 装完让下一次体检不吃旧缓存

    threading.Thread(target=_run, daemon=True).start()
    return {"jobId": job_id, "id": tid, "state": "running"}


@app.get("/api/env/job/{job_id}")
async def api_env_job(job_id: str):
    """查安装进度：running / ok / fail + 最近日志行 + 最终结果。"""
    job = _ENV_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在（服务可能重启过）")
    return job


# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 模型配置（v1：按真实 .env 只读展示 + 真自测）


# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 环境安装（skills/shared/scripts/install_tool.py 引擎桥）
# 体检走引擎 check --json；安装后台跑、前端轮询 /api/env/job/{id}。
# ═══════════════════════════════════════════════════════════════════════

INSTALL_TOOL = SHARED_SCRIPTS / "install_tool.py"
_ENV_TOOLS_CACHE: dict = {"ts": 0.0, "data": None}
_ENV_JOBS: dict[str, dict] = {}


@app.get("/api/env/tools")
async def api_env_tools(refresh: bool = False):
    """环境体检：引擎 check --json（15 秒缓存；refresh=1 强制重测）。"""
    if not refresh and _ENV_TOOLS_CACHE["data"] and time.time() - _ENV_TOOLS_CACHE["ts"] < 15:
        return _ENV_TOOLS_CACHE["data"]
    try:
        proc = await asyncio.to_thread(lambda: subprocess.run(
            [sys.executable, str(INSTALL_TOOL), "--json", "check"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=240, cwd=str(PROJECT_ROOT)))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "环境体检超时，请稍后再试")
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise HTTPException(500, f"环境体检失败：{(proc.stderr or '').strip()[-300:] or '无输出'}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"体检输出解析失败：{e}")
    data["cachedAt"] = int(time.time())
    _ENV_TOOLS_CACHE.update({"ts": time.time(), "data": data})
    return data


class EnvInstallRequest(BaseModel):
    id: str


_INSTALL_IDS_CACHE: dict = {"ts": 0.0, "ids": frozenset()}


def _install_tool_ids() -> frozenset[str]:
    """引擎里真实存在的配方 id（缓存 5 分钟）。安装接口只认这张表里的 id——
    只按正则放行的话，`--help` 这类带横线的串会被 argparse 当选项吃掉。"""
    if _INSTALL_IDS_CACHE["ids"] and time.time() - _INSTALL_IDS_CACHE["ts"] < 300:
        return _INSTALL_IDS_CACHE["ids"]
    try:
        p = subprocess.run([sys.executable, str(INSTALL_TOOL), "--json", "list"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30, cwd=str(PROJECT_ROOT))
        ids = frozenset(t["id"] for t in (json.loads(p.stdout).get("tools") or []) if t.get("id"))
    except Exception:  # noqa: BLE001
        return _INSTALL_IDS_CACHE["ids"]
    if ids:
        _INSTALL_IDS_CACHE.update({"ts": time.time(), "ids": ids})
    return ids


@app.post("/api/env/install")
async def api_env_install(req: EnvInstallRequest):
    """后台安装（引擎 install）：立即返回 jobId，前端轮询进度。"""
    tid = (req.id or "").strip()
    known = await asyncio.to_thread(_install_tool_ids)
    if tid not in known:
        raise HTTPException(400, f"无效的工具 id：{tid[:40]!r}")
    job_id = uuid.uuid4().hex[:16]
    if len(_ENV_JOBS) >= 200:                   # 任务表不能无限长：清掉最老的已结束任务
        for k, _ in sorted((kv for kv in _ENV_JOBS.items() if kv[1].get("ended")),
                           key=lambda kv: kv[1]["ended"])[:100]:
            _ENV_JOBS.pop(k, None)
    job = {"jobId": job_id, "id": tid, "state": "running", "lines": [],
           "result": None, "started": int(time.time()), "ended": None}
    _ENV_JOBS[job_id] = job

    def _run() -> None:
        proc = None
        try:
            proc = subprocess.Popen(
                [sys.executable, str(INSTALL_TOOL), "--json", "install", tid],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT))
            # stdout 必须**并发**抽干：串行地先读完 stderr 再读 stdout，子进程一旦往 stdout
            # 写满管道缓冲（64K）就会阻塞，而我们还堵在 stderr 上——双向死锁。
            _out: list[str] = []
            t_out = threading.Thread(target=lambda: _out.append(proc.stdout.read() or ""), daemon=True)
            t_out.start()
            for line in proc.stderr:            # 引擎进度日志走 stderr
                line = line.rstrip()
                if line:
                    job["lines"] = (job["lines"] + [line])[-40:]   # 只留最近 40 行（换列表，避免读端正在序列化时被就地改）
            try:
                proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:   # 卡死的安装进程必须真的杀掉，否则线程与子进程永久泄漏
                proc.kill()
                proc.wait(timeout=30)
                raise
            t_out.join(timeout=30)
            out = ("".join(_out)).strip()
            if out:
                job["result"] = (json.loads(out).get("results") or [None])[0]
            job["state"] = "ok" if (job["result"] or {}).get("state") == "ok" else "fail"
        except Exception as e:  # noqa: BLE001
            job["state"] = "fail"
            job["result"] = {"id": tid, "state": "fail", "detail": f"{type(e).__name__}: {e}"}
        finally:
            job["ended"] = int(time.time())
            _ENV_TOOLS_CACHE["ts"] = 0.0        # 装完让下一次体检不吃旧缓存

    threading.Thread(target=_run, daemon=True).start()
    return {"jobId": job_id, "id": tid, "state": "running"}


@app.get("/api/env/job/{job_id}")
async def api_env_job(job_id: str):
    """查安装进度：running / ok / fail + 最近日志行 + 最终结果。"""
    job = _ENV_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在（服务可能重启过）")
    return job


# ═══════════════════════════════════════════════════════════════════════
# 设置面板 · 模型配置（v1：按真实 .env 只读展示 + 真自测）


# ═══════════════════════════════════════════════════════════════════════


def _mask_key(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    return f"«{v[:5]}…{v[-4:]}»" if len(v) > 14 else "«已配置»"


def _model_channels(runtime: str | None = None) -> dict:
    env = _read_env()
    primary = ""
    direct = runtime == "api" if runtime else _direct_api_enabled()
    if direct:
        env = read_settings(ENV_FILE)
        primary = f"direct-api/{env.get('EASEL_DIRECT_API_MODEL', '')}"
    direct_ready = False
    if direct:
        try:
            api_config(env)
            direct_ready = True
        except DirectAPIError:
            pass

    chat_rows = []
    ob = (env.get("OPENAI_BASE_URL") or "").strip()
    om = (env.get("OPENAI_MODEL") or "").strip()
    ok_key = _is_set(env.get("OPENAI_API_KEY"))
    if direct:
        chat_rows.append({
            "slot": "direct-api", "order": 1, "name": "本地 / 内网 API 直连",
            "sub": "独立直连配置", "type": "openai",
            "model": env.get("EASEL_DIRECT_API_MODEL", "").strip(),
            "baseUrl": env.get("EASEL_DIRECT_API_BASE_URL", "").strip(),
            "keyMasked": _mask_key(env.get("EASEL_DIRECT_API_KEY", "")),
            "role": "主", "result": "已配置" if direct_ready else "缺配置",
        })
    elif ob or ok_key:
        chat_rows.append({
            "slot": "openai", "order": 1, "name": "deepseek",
            "sub": "官方直连",
            "type": "openai", "model": om or "deepseek-chat",
            "baseUrl": ob, "keyMasked": _mask_key(env.get("OPENAI_API_KEY", "") if ok_key else ""),
            "role": "主" if primary.startswith("openai/") else "备",
            "result": "已配置" if ok_key else "缺 key",
        })
    def _is_primary(pkey: str, model: str) -> bool:
        """这一行是不是当前的主模型。

        不能像 openai 行那样只比 provider 前缀：anthropic 与 relay 两个槽位现在都落在
        同一个 anthropic provider 上，只比前缀会让两行
        同时显示「主」。所以连模型一起比；model 本身可能已是 provider/model 形式
        （CLAUDE_MODEL 按约定就这么写），两种写法都要认。
        """
        if not primary or not model:
            return False
        return primary == model or primary == f"{pkey}/{model}"

    ab = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    ak = (env.get("ANTHROPIC_API_KEY") or "").strip()
    ak_ok = _is_set(ak)
    if not direct and (ab or ak):
        chat_rows.append({
            "slot": "anthropic", "order": len(chat_rows) + 1, "name": "anthropic", "sub": "官方直连",
            "type": "anthropic", "model": (env.get("CLAUDE_MODEL") or "claude-sonnet-4-6").strip(),
            "baseUrl": ab or "官方", "keyMasked": _mask_key(ak if ak_ok else ""),
            # 原来写死「备」：主模型明明是 anthropic/... 时面板也显示备用，和 openai 行
            # 的处理不一致，用户据此判断「哪个在生效」会判错。
            "role": "主" if _is_primary("anthropic", (env.get("CLAUDE_MODEL") or "").strip()) else "备",
            "result": "已配置" if ak_ok else "缺 key",
        })
    lb = (env.get("EASEL_LLM_BASE_URL") or "").strip()
    lk = (env.get("EASEL_LLM_API_KEY") or "").strip()
    lk_ok = _is_set(lk)
    if not direct and (lb or lk):
        chat_rows.append({
            "slot": "relay", "order": len(chat_rows) + 1, "name": "relay", "sub": "中转站",
            "type": "openai", "model": (env.get("CLAUDE_MODEL") or "deepseek-chat").strip(),
            "baseUrl": lb or "（未配置）", "keyMasked": _mask_key(lk if lk_ok else ""),
            # relay 槽位落的也是 anthropic provider（与 setup.sh 的 EASEL_LLM_* 分支一致）
            "role": "主" if _is_primary("anthropic", (env.get("CLAUDE_MODEL") or "").strip()) else "备",
            "result": "已配置" if lk_ok else "缺 key",
        })

    chat_rows.extend([])

    sf = bool((env.get("SILICONFLOW_API_KEY") or "").strip())
    trans_rows = [
        {"order": 0, "name": "自带字幕", "sub": "视频自带 SRT/VTT 时直接读", "type": "脚本层",
         "model": "—", "baseUrl": "—", "keyMasked": "—", "role": "免配", "result": "优先"},
        {"slot": "siliconflow", "order": 1, "name": "siliconflow", "sub": "硅基流动", "type": "openai",
         "model": "SenseVoiceSmall", "baseUrl": "https://api.siliconflow.cn/v1",
         "keyMasked": _mask_key(env.get("SILICONFLOW_API_KEY", "")), "role": "主",
         "result": "已配置" if sf else "缺 key"},
    ]
    channels: dict = {"chat": {"rows": chat_rows}, "transcribe": {"rows": trans_rows}}
    try:
        import model_registry as _mr  # skills/shared/scripts 已在 sys.path 上
        _setting_env = {"video": "VIDEO_PROVIDER", "music": "MUSIC_PROVIDER", "voice": "VOICE_PROVIDER"}
        for _gid, _ch in (("image", "image"), ("video", "video"), ("music", "music"), ("voice", "speech")):
            _spec = _mr.MODEL_GROUPS[_gid]
            _chosen = (env.get(_setting_env.get(_gid, ""), "") or "").strip()
            _rows = []
            for _p in _spec["providers"]:
                _req = [k for k in _p["keys"] if k["required"]]
                _bk = next((k for k in _p["keys"] if not k["secret"]
                            and ("BASE" in k["env"] or "URL" in k["env"])), None)
                _mk = next((k for k in _p["keys"] if not k["secret"] and "MODEL" in k["env"]), None)
                _edit_keys = [k for k in _req if k is not _bk and k is not _mk]
                _k1 = _edit_keys[0] if _edit_keys else None
                _k2 = _edit_keys[1] if len(_edit_keys) > 1 else None
                _ok = all(_is_set(env.get(k["env"])) or any(_is_set(env.get(a)) for a in k.get("aliases", []))
                          for k in _req)
                _masked = ""
                if _k1:
                    _raw = env.get(_k1["env"], "")
                    if not _raw:
                        for _a in _k1.get("aliases", []):
                            if env.get(_a):
                                _raw = env[_a]
                                break
                    _masked = _mask_key(_raw)
                _rows.append({
                    "slot": _p["id"], "order": 0, "name": _p["name"], "sub": _gid,
                    "type": _p["id"], "model": (env.get(_mk["env"]) or "").strip() if _mk else "",
                    "baseUrl": (env.get(_bk["env"]) or "").strip() if _bk else "",
                    "keyMasked": _masked, "role": "主" if (_gid == "image" or _chosen == _p["id"]) else "备",
                    "result": "已配置" if _ok else "未配置",
                    "key2Label": _k2["label"] if _k2 else "",
                    "key2Masked": _mask_key(env.get(_k2["env"], "")) if _k2 and _k2["secret"] else "",
                    "modelEditable": _mk is not None,
                    "baseEditable": _bk is not None,
                    "baseOptional": (_bk is None) or (not _bk["required"]),
                })
            channels[_ch] = {"rows": _rows}
    except Exception:  # noqa: BLE001
        pass
    return {"channels": channels, "primary": primary,
            "transport": "api" if direct else (runtime or "")}


class ChatTransportRequest(BaseModel):
    transport: str


class HarnessSettingsRequest(BaseModel):
    runtime: str
    model: str = Field(default="", max_length=240)
    sandbox: str = "workspace-write"
    codex_auto_approve: StrictBool | None = None


@app.get("/api/settings/harness")
async def api_harness_settings():
    config = _harness_settings()
    agents = await asyncio.to_thread(lambda: [harnesses.describe(runtime) for runtime in harness_store.IDS])
    return {**config, "agents": agents,
            "locked": "EASEL_AGENT_RUNTIME" in read_settings(ENV_FILE)}


@app.post("/api/settings/harness")
async def api_harness_settings_save(req: HarnessSettingsRequest):
    if req.runtime not in harness_store.IDS:
        raise HTTPException(400, "未知 Agent 底座")
    if req.sandbox not in ("read-only", "workspace-write"):
        raise HTTPException(400, "请选择只读或工作区写入权限")
    if any(ord(character) < 32 for character in req.model):
        raise HTTPException(400, "模型名称不能包含控制字符")
    if "EASEL_AGENT_RUNTIME" in read_settings(ENV_FILE):
        raise HTTPException(409, "启动环境固定了底座，请修改 EASEL_AGENT_RUNTIME 后重启")
    if _RUNNING_CHAT or _BG_TASKS or _DIRECT_CHAT_TASKS or _HARNESS_TASKS:
        raise HTTPException(409, "请等待当前任务完成或停止后切换底座")
    if req.runtime != "api" and not harnesses.describe(req.runtime)["installed"]:
        raise HTTPException(409, "请先在终端安装并配置对应 Agent CLI，再切换")
    config = _harness_settings()
    config["runtime"] = req.runtime
    config["sandbox"] = req.sandbox
    if req.runtime == "codex" and req.codex_auto_approve is not None:
        config["codex_auto_approve"] = req.codex_auto_approve
    config["models"] = {**config["models"], req.runtime: req.model.strip()}
    harness_store.write_json(OUTPUTS_DIR / "_harness" / "config.json", config)
    from easel.harnesses.rpc import close_sessions
    await close_sessions()
    return await api_harness_settings()


@app.get("/api/settings/models")
async def api_settings_models(runtime: str | None = None):
    """模型通道：chat / transcribe（含可编辑的原值；key 只回脱敏）。"""
    if runtime is not None and runtime != "api":
        raise HTTPException(400, "仅聊天模式有 API 供应商配置")
    if runtime is None:
        return await asyncio.to_thread(_model_channels)
    return await asyncio.to_thread(_model_channels, runtime)


@app.post("/api/settings/harness/models/{runtime}")
async def api_harness_models(runtime: str):
    if runtime not in ("codex", "opencode", "pi"):
        raise HTTPException(400, "此底座不支持原生模型列表")
    from easel.harnesses.base import RunRequest
    from easel.harnesses.models import available_models
    request = RunRequest("", PROJECT_ROOT, {
        **_proxy_env(), "EASEL_ROOT": str(PROJECT_ROOT), "EASEL_AGENT_RUNTIME": runtime})
    try:
        models = await asyncio.wait_for(available_models(runtime, request), 25)
    except asyncio.TimeoutError as error:
        raise HTTPException(504, "原生模型列表读取超时，请稍后重试") from error
    except (HarnessError, OSError, UnicodeError) as error:
        raise HTTPException(409, "无法读取原生模型列表，请检查 CLI 安装与原生配置") from error
    return {"runtime": runtime, "models": models}


def _write_env_direct(updates: dict[str, str]) -> None:
    """后端受控键位专用：就地更新/追加 .env（不做 allowlist 过滤，仅服务端固定映射调用）。原子写。"""
    updates = {k: v for k, v in updates.items() if k and v.strip() != ''}
    if not updates:
        return
    _guard_env_values(updates)
    lines = ENV_FILE.read_text(encoding='utf-8').splitlines() if ENV_FILE.is_file() else []
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        matched = None
        if s and not s.startswith('#') and '=' in s:
            k = s.split('=', 1)[0].strip()
            if k in updates:
                matched = k
        if matched is not None:
            seen.add(matched)
            out.append(f'{matched}={updates[matched]}')
            continue
        out.append(line)
    appended = [f'{k}={v}' for k, v in updates.items() if k not in seen]
    if appended:
        if out and out[-1].strip() != '':
            out.append('')
        out.append('# ---- Easel 模型配置（Web 设置面板写入）----')
        out.extend(appended)
    tmp = ENV_FILE.with_suffix('.env.tmp')
    tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
    tmp.replace(ENV_FILE)


RESERVED_PROVIDER_KEYS = {"openai", "anthropic", "relay"}
THINKING_FORMATS = {
    "openai", "openrouter", "deepseek", "together", "qwen", "qwen-chat-template", "zai",
}
THINKING_COMPAT_KEYS = {
    "thinkingFormat", "supportsReasoningEffort", "supportedReasoningEfforts", "reasoningEffortMap",
}





class ModelSaveRow(BaseModel):
    slot: str = ""
    name: str = ""
    model: str = ""
    baseUrl: str = ""
    key: str = ""
    key2: str = ""
    primary: bool = False
    # 自定义供应商的上游协议：openai（默认，/chat/completions）或 anthropic
    # （原生 /v1/messages；中转站卖原生 Claude 格式时选它）。
    protocol: str = ""
    thinking: StrictBool | None = None
    thinkingFormat: str | None = None


class ModelSaveRequest(BaseModel):
    channel: str = "chat"
    rows: list[ModelSaveRow] = Field(default_factory=list)


@app.post("/api/settings/models/save")
async def api_settings_models_save(req: ModelSaveRequest):
    """保存模型通道：.env 就地更新（key 留空=不改）；媒体通道写 provider 配置。"""
    ch0 = (req.channel or "").strip()
    if ch0 == "chat" and not _direct_api_enabled():
        raise HTTPException(409, "当前底座使用原生认证；模型覆盖请在 Agent 底座设置中保存")
    direct = _direct_api_enabled()
    if direct and ch0 == "chat" and any(row.slot != "direct-api" for row in req.rows):
        raise HTTPException(400, "API 直连模式请使用独立的 API 直连通道")
    if any(row.slot == "direct-api" for row in req.rows) and not (direct and ch0 == "chat"):
        raise HTTPException(400, "请先切换到 API 直连模式再保存直连配置")
    if direct and ch0 == "chat" and any(
            name in os.environ for name in (
                "EASEL_DIRECT_API_BASE_URL", "EASEL_DIRECT_API_MODEL", "EASEL_DIRECT_API_KEY")):
        raise HTTPException(409, "运行环境已指定直连配置，请修改启动环境后重启")
    if ch0 in ("speech", "image", "video", "music"):
        import model_registry as _mr2
        _gid0 = {"speech": "voice", "image": "image", "video": "video", "music": "music"}[ch0]
        _spec0 = _mr2.MODEL_GROUPS[_gid0]
        _by_id = {p["id"]: p for p in _spec0["providers"]}
        _setting0 = {"video": "VIDEO_PROVIDER", "music": "MUSIC_PROVIDER", "voice": "VOICE_PROVIDER"}.get(_gid0)
        _mupd: dict[str, str] = {}
        _primary0 = ""
        _env0 = _read_env()
        for _row in req.rows:
            _pid = (_row.slot or "").strip()
            _p0 = _by_id.get(_pid)
            if not _p0:
                raise HTTPException(400, f"不认识的服务商：{_pid or '（空）'}")
            _base0 = (_row.baseUrl or "").strip().rstrip("/")
            _key0 = (_row.key or "").strip()
            _key20 = (_row.key2 or "").strip()
            _model0 = (_row.model or "").strip()
            if _base0 and not _valid_base_url(_base0):
                raise HTTPException(400, f'{_p0["name"]} 的根地址需是合法的 http(s):// 地址')
            if (_key0 and any(x.isspace() for x in _key0)) or (_key20 and any(x.isspace() for x in _key20)):
                raise HTTPException(400, f'{_p0["name"]} 的 Key 不能包含空白字符')
            _bk0 = next((k for k in _p0["keys"] if not k["secret"]
                         and ("BASE" in k["env"] or "URL" in k["env"])), None)
            _mk0 = next((k for k in _p0["keys"] if not k["secret"] and "MODEL" in k["env"]), None)
            _req0 = [k for k in _p0["keys"] if k["required"] and k is not _bk0 and k is not _mk0]
            # 与对话通道同一条规矩：只改根地址、Key 留空（留空=沿用旧 Key），等于把已存的真 Key
            # 指到新地址去——SILICONFLOW 这类通道还有「连通性自测」会把它当 Bearer 发过去。
            if _base0 and _bk0 and not _key0 and _base0 != (_env0.get(_bk0["env"], "") or "").strip().rstrip("/") \
                    and _req0 and (_env0.get(_req0[0]["env"], "") or "").strip():
                raise HTTPException(400, f'更换{_p0["name"]}的根地址时必须重新填写 Key')
            if _key0 and _req0:
                _mupd[_req0[0]["env"]] = _key0
            if _key20 and len(_req0) > 1:
                _mupd[_req0[1]["env"]] = _key20
            if _model0 and _mk0:
                _mupd[_mk0["env"]] = _model0
            if _base0 and _bk0:
                _mupd[_bk0["env"]] = _base0
            if getattr(_row, "primary", False):
                _primary0 = _pid
        if _primary0 and _setting0:
            _mupd[_setting0] = _primary0
        if not _mupd:
            raise HTTPException(400, "没有可保存的改动（key 留空表示不改）")
        _write_env_direct(_mupd)
        _resp0 = {"ok": True, "note": ""}
        _resp0.update(_model_channels())
        return _resp0

    updates: dict[str, str] = {}
    provider_updates: dict[str, dict] = {}
    keep_custom: set[str] = set()
    primary_ref = ''
    is_chat = (req.channel or '').strip() == 'chat'
    _cur_env = read_settings(ENV_FILE) if direct else _read_env()
    _cur_prov: dict[str, tuple[str, str]] = {}  # 供应商镜像已不再维护（保留空表以兼容下方读取）
    for row in req.rows:
        slot = (row.slot or '').strip()
        name = (row.name or '').strip().lower()
        model = (row.model or '').strip()
        base = (row.baseUrl or '').strip().rstrip('/')
        key = (row.key or '').strip()
        if base and not _valid_base_url(base):
            raise HTTPException(400, f'Base URL 需是合法的 http(s):// 地址：{base[:60]}')
        if key and any(ch.isspace() for ch in key):
            raise HTTPException(400, 'API Key 不能包含空白字符')
        if len(model) > 120 or len(base) > 300 or len(key) > 400:
            raise HTTPException(400, '字段过长，请检查')
        # 改 Base URL 但把 Key 留空（留空=沿用旧 Key）＝ 把已存的真 Key 指向新地址，
        # 之后一次「连通性自测」就会把它当 Bearer 送到新地址去。换地址必须重填 Key。
        _be, _ke = _SLOT_ENV_KEYS.get(slot, ('', ''))
        if base and _be and not key and base != (_cur_env.get(_be, '') or '').strip().rstrip('/') \
                and (_cur_env.get(_ke, '') or '').strip():
            raise HTTPException(400, f'更换 Base URL 时必须重新填写 API Key（{slot}）')
        pkey = ''
        if slot == 'direct-api':
            if model:
                updates['EASEL_DIRECT_API_MODEL'] = model
            if base:
                updates['EASEL_DIRECT_API_BASE_URL'] = base
            if key:
                updates['EASEL_DIRECT_API_KEY'] = key
        elif slot == 'openai':
            if model:
                updates['OPENAI_MODEL'] = model
            if base:
                updates['OPENAI_BASE_URL'] = base
            if key:
                updates['OPENAI_API_KEY'] = key
            if is_chat:
                provider_updates['openai'] = {'model': model, 'base': base, 'key': key}
                pkey = 'openai'
        elif slot == 'relay':
            if base:
                updates['EASEL_LLM_BASE_URL'] = base
            if model:
                updates['CLAUDE_MODEL'] = model
            if key:
                updates['EASEL_LLM_API_KEY'] = key
            if is_chat:
                # 落进 anthropic provider，而不是造一个名叫 relay 的 provider。
                # setup.sh 的 EASEL_LLM_* 分支写的就是 models.providers.anthropic
                # （见 oc_write_anthropic）。此前这里只写 .env、从不创建 provider，
                # 却把 primary 指向 relay/<model> —— 对话直接报
                # 「No route-compatible authentication source is configured for relay」。
                pkey = 'anthropic'
        elif slot == 'anthropic':
            if model:
                updates['CLAUDE_MODEL'] = model
            if key:
                updates['ANTHROPIC_API_KEY'] = key
            if base:
                updates['ANTHROPIC_BASE_URL'] = base
            if is_chat:
                pkey = 'anthropic'
        elif slot == 'siliconflow':
            if base:
                updates['SILICONFLOW_BASE_URL'] = base
            if key:
                updates['SILICONFLOW_API_KEY'] = key
        elif slot == 'custom' and is_chat:
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,23}', name):
                raise HTTPException(400, f'供应商名只能用小写字母/数字/横线（最长24位）：{name[:30] or "（空）"}')
            if name in RESERVED_PROVIDER_KEYS:
                raise HTTPException(400, f'「{name}」是内置槽位名，请换一个')
            if not model or not base:
                raise HTTPException(400, f'自定义供应商「{name}」需要同时填模型和 Base URL')
            proto = (getattr(row, 'protocol', '') or '').strip().lower()
            if proto not in ('', 'openai', 'anthropic'):
                raise HTTPException(400, f'协议只支持 openai / anthropic：{proto}')
            thinking_format = (row.thinkingFormat or '').strip().lower()
            if thinking_format and thinking_format not in THINKING_FORMATS:
                raise HTTPException(400, f'不支持的思考格式：{thinking_format}')
            if thinking_format and row.thinking is None:
                raise HTTPException(400, '选择思考格式时需要显式声明是否启用思考')
            if proto == 'anthropic' and thinking_format:
                raise HTTPException(400, 'Anthropic 原生协议无需选择 OpenAI 兼容思考格式')
            provider_updates[name] = {'model': model, 'base': base, 'key': key, 'protocol': proto}
            if row.thinking is not None:
                provider_updates[name]['thinking'] = row.thinking
                if thinking_format:
                    provider_updates[name]['thinkingFormat'] = thinking_format
            keep_custom.add(name)
            pkey = name
        # 同一条规矩也覆盖配置侧：只换地址、Key 留空，下一轮对话就会拿原 Key 去打新地址。
        # 自定义供应商不写 .env，前面那道 _SLOT_ENV_KEYS 闸拦不到它。
        # 但本地回环代理地址要放行：它存的地址与真实上游永远不相等，
        # 那道闸会把这类供应商彻底锁死（连只改模型都保存不了）。
        if is_chat and pkey and base and not key:
            # 换址校验的「现值」要跟配置的权威来源比：openai/relay/anthropic 的权威在 .env
            # （面板与 setup.sh 都写它），配置侧只是保存时的镜像——setup.sh 配置后
            # 用户从没用面板保存过的话，镜像可能停在初始值（如 api.openai.com），拿它比对
            # 会把「原样回传真值」误判成「换址没填 key」，整个面板都保存不了（真机踩中）。
            # custom 供应商的权威在配置侧，维持原比对。
            _be, _ke = _SLOT_ENV_KEYS.get(slot, ('', ''))
            if _be and _is_set(_cur_env.get(_ke)):
                _pb = (_cur_env.get(_be, '') or '').strip().rstrip('/')
                _pk = (_cur_env.get(_ke, '') or '').strip()
                if slot == 'anthropic' and not _pb:
                    _pb = 'https://api.anthropic.com'
            else:
                _pb, _pk = _cur_prov.get(pkey, ('', ''))
                _pb = _pb.strip().rstrip('/')
            if _pk and base != _pb and not _is_local_gateway_base(_pb):
                raise HTTPException(400, f'更换 Base URL 时必须重新填写 API Key（{pkey}）')
        if is_chat and slot == 'openai' and not key and _is_set(_cur_env.get('OPENAI_API_KEY')) \
                and (_cur_env.get('OPENAI_BASE_URL') or '').strip() \
                and not _is_local_gateway_base(_cur_prov.get('openai', ('', ''))[0]):
            provider_updates['openai']['key'] = _cur_env['OPENAI_API_KEY'].strip()
            provider_updates['openai']['base'] = (
                base or _cur_env['OPENAI_BASE_URL'].strip().rstrip('/'))
        if is_chat and pkey and getattr(row, 'primary', False) and model:
            # model 可能本来就是 provider/model 形式：anthropic 行的 model 直接取自
            # .env 的 CLAUDE_MODEL，而那个值按约定就写成 anthropic/claude-opus-4-7。
            # 无脑拼前缀会写出 anthropic/anthropic/claude-opus-4-7，主模型随即不可路由。
            # 自定义供应商的模型 ID 是上游原值，可能包含命名空间
            # （如 LiteLLM 的 dashscope/qwen-*），必须保留并加上实际供应商。
            primary_ref = (f'{pkey}/{model}' if slot == 'custom'
                           else model if model.startswith(f'{pkey}/') else f'{pkey}/{model}')
    if not updates and not provider_updates and not primary_ref:
        raise HTTPException(400, '没有可保存的改动（key 留空表示不改）')
    if is_chat and direct:
        try:
            api_config({**_cur_env, **updates})
        except DirectAPIError as error:
            raise HTTPException(400, str(error)) from None
    if updates:
        _write_env_direct(updates)
    note = ''
    if is_chat:
        note = "API 直连配置已保存，下一轮生效"
    resp = {"ok": True, "note": note}
    resp.update(_model_channels())
    return resp


class LocalAgentEnableRequest(BaseModel):
    id: str
    # 可选：从插件模型目录里选的模型 id（前端下拉列表）；不传用各家默认。
    model: str = ""


@app.get("/api/settings/local-agents")
async def api_local_agents():
    """探测本机已装的 agent CLI（Claude Code / Gemini CLI / Codex…）。

    目的：如实标出本机装了哪些 agent CLI 及其登录状态，供界面展示，不假装支持。
    """
    from easel.local_agents import summarize_local_agents
    return await asyncio.to_thread(summarize_local_agents)


@app.post("/api/settings/local-agents/enable")
async def api_local_agent_enable(req: LocalAgentEnableRequest):
    """把一个本机 CLI agent 接入 Easel 的提示接口。

    历史上该接入由已移除的常驻网关完成；网关移除后，此接口不再写配置，
    只返回一条说明性 note，引导用户改用 Agent 底座（Codex / OpenCode / ACP / Pi）
    来驱动这些 CLI。
    """
    agents = {a["id"]: a for a in detect_local_agents()}
    agent = agents.get(req.id)
    if not agent:
        raise HTTPException(404, f"未知的本机 agent：{req.id}")
    # 本机 CLI agent 的「免 Key 接入」原先由常驻网关完成，该网关已移除。
    # 返回说明性 note，让前端如实告知用户：改用 Agent 底座（Codex / OpenCode / ACP / Pi）。
    note = (f"{agent['label']} 的免 Key 接入已不再支持；"
            "如需使用请改用 Agent 底座（Codex / OpenCode / ACP / Pi）在 Agent 底座设置里配置。")
    return {"ok": True, "agent": agent, "note": note}




class SelftestRequest(BaseModel):
    channel: str = "chat"


class ModelsFetchRequest(BaseModel):
    """「拉取模型列表」请求。

    key 优先用前端传来的草稿值（供应商还没落盘时也能拉）；留空则按 slot 从已存配置里取
    —— 用户已经存过 Key，不该为了拉一次列表再贴一遍明文。服务端全程不落盘、不写日志。

    baseUrl 允许留空由 slot 回落，否则「只用已存配置拉列表」这条路径就进不来。
    """
    baseUrl: str = ""
    key: str = ""
    protocol: str = "openai"  # openai | anthropic
    slot: str = ""
    name: str = ""  # 自定义供应商的 provider 名（slot='custom' 时用它查已存凭据）


# slot → (base 键, key 键)；与 _SLOT_ENV_KEYS 同源，外加自定义供应商。
_FETCH_KEY_BY_SLOT = {
    'direct-api': ('EASEL_DIRECT_API_BASE_URL', 'EASEL_DIRECT_API_KEY'),
    'openai': ('OPENAI_BASE_URL', 'OPENAI_API_KEY'),
    'relay': ('EASEL_LLM_BASE_URL', 'EASEL_LLM_API_KEY'),
    'anthropic': ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY'),
    'siliconflow': ('SILICONFLOW_BASE_URL', 'SILICONFLOW_API_KEY'),
}


@app.post("/api/settings/models/available")
async def api_models_available(req: ModelsFetchRequest):
    """代前端拉一次 GET {base}/models，返回模型 id 列表（自定义供应商不用再手打模型名）。

    与 selftest 同一条安全线：带着用户 Key 出去的请求，目标必须过 _valid_base_url +
    _ssrf_safe；不落盘、不写日志。
    """
    base = (req.baseUrl or "").strip().rstrip("/")
    key = (req.key or "").strip()
    slot = (req.slot or "").strip()
    # 草稿没填 key 时，回落到该槽位已存的值（只在内存里用，不回显、不记日志）。
    if not key and slot:
        _env = read_settings(ENV_FILE) if _direct_api_enabled() else _read_env()
        if slot == 'custom':
            # 自定义供应商凭据不再写入配置。
            _b, _k = "", ""
        else:
            _bk, _kk = _FETCH_KEY_BY_SLOT.get(slot, ("", ""))
            _b, _k = _env.get(_bk, ""), _env.get(_kk, "")
        key = (_k or "").strip()
        if slot == "direct-api" and key and base and base != (_b or "").strip().rstrip("/"):
            raise HTTPException(400, "更换根地址时必须重新填写 Key，不能把已存 Key 发给其他地址")
        if not base and _b:
            base = _b.strip().rstrip("/")
    if not base:
        raise HTTPException(400, "Base URL 不能为空")
    if not _valid_base_url(base):
        raise HTTPException(400, "Base URL 不合法")
    if not _model_target_allowed(base, slot):
        raise HTTPException(400, "目标指向本机/内网地址，已拒绝（避免把 API Key 发给内网服务）")

    anthropic = slot != "direct-api" and (req.protocol or "").strip().lower() == "anthropic"
    if anthropic:
        url = base + "/v1/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                   "Authorization": f"Bearer {key}"}
    else:
        url = base + "/models"
        headers = {"Authorization": f"Bearer {key}"} if key else {}

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    handlers = [_NoRedirect]
    if slot == "direct-api" and _direct_api_enabled() and base == read_settings(ENV_FILE).get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/"):
        handlers.append(urllib.request.ProxyHandler({}))
    _opener = urllib.request.build_opener(*handlers)

    def _fetch() -> list[str]:
        rq = urllib.request.Request(url, headers=headers)
        with _opener.open(rq, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        # OpenAI / Anthropic 形状都是 {"data":[{"id":...}]}；有的网关直接给 ["id",...]。
        if isinstance(payload, dict):
            items = payload.get("data") or payload.get("models") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        ids = []
        for it in items:
            mid = it.get("id") if isinstance(it, dict) else it
            if isinstance(mid, str) and mid.strip():
                ids.append(mid.strip())
        return sorted(set(ids))

    try:
        models = await asyncio.to_thread(_fetch)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"拉取失败：{type(e).__name__}: {e}"[:180]) from e
    return {"baseUrl": base, "models": models, "fetchedAt": int(time.time())}


@app.post("/api/settings/models/selftest")
async def api_models_selftest(req: SelftestRequest):
    """真自测：对已配置的 OpenAI 兼容通道发 GET {base}/models 并计耗时。"""
    channel = (req.channel or "all").strip()
    if channel in ("chat", "all") and not _direct_api_enabled():
        raise HTTPException(409, "此底座使用原生模型配置，请新建对话或运行 easel ping 验证")
    direct = _direct_api_enabled()
    env = read_settings(ENV_FILE) if direct else _read_env()
    # (base, key, 是否为 Anthropic Messages 协议, slot)。协议决定鉴权头与路径：
    # Anthropic 是 x-api-key + /v1/models，OpenAI 兼容是 Bearer + /models。
    targets: list[tuple[str, str, bool, str]] = []
    if channel in ("chat", "all"):
        if direct:
            chat_targets = ((env.get("EASEL_DIRECT_API_BASE_URL", ""),
                             env.get("EASEL_DIRECT_API_KEY", ""), False, "direct-api"),)
        else:
            chat_targets = (
                (env.get("ANTHROPIC_BASE_URL", ""), env.get("ANTHROPIC_API_KEY", ""), True, "anthropic"),
                (env.get("OPENAI_BASE_URL", ""), env.get("OPENAI_API_KEY", ""), False, "openai"),
                (env.get("EASEL_LLM_BASE_URL", ""), env.get("EASEL_LLM_API_KEY", ""), False, "relay"),
            )
        for base, key, is_anthropic, slot in chat_targets:
            if base.strip() and (key.strip() or direct):
                targets.append((base.strip().rstrip("/"), key.strip(), is_anthropic, slot))
    if channel in ("transcribe", "all") and (env.get("SILICONFLOW_API_KEY") or "").strip():
        targets.append(((env.get("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1").strip().rstrip("/"),
                        env["SILICONFLOW_API_KEY"].strip(), False, "siliconflow"))

    # 这里会把真实 API Key 当 Bearer 发出去，所以目标地址必须先过闸：
    # 合法 http(s)、且不指向本机/内网/云元数据；跳转也不跟（跟了等于绕过前面的判断）。
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_a, **_kw):
            return None

    _opener = urllib.request.build_opener(_NoRedirect)

    def _probe(base: str, key: str, *, anthropic: bool = False, slot: str = "") -> dict:
        t0 = time.time()
        if not _valid_base_url(base):
            return {"baseUrl": base, "ok": False, "ms": 0, "detail": "Base URL 不合法，未发起请求"}
        if not _model_target_allowed(base, slot):
            return {"baseUrl": base, "ok": False, "ms": 0,
                    "detail": "目标指向本机/内网地址，已拒绝（避免把 API Key 发给内网服务）"}
        try:
            if anthropic:
                # Anthropic Messages 协议的鉴权头是 x-api-key（不是 Authorization: Bearer），
                # 且模型列表在 /v1/models。按 OpenAI 兼容方式探测只会得到 401/404，
                # 让用户误以为 Key 无效。同时带上两种头：兼容把 /v1/models 反代成
                # OpenAI 风格的中转站（它们只认 Bearer）。
                url = base + "/v1/models"
                rq = urllib.request.Request(url, headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "Authorization": f"Bearer {key}",
                })
            else:
                headers = {"Authorization": f"Bearer {key}"} if key else {}
                rq = urllib.request.Request(base + "/models", headers=headers)
            opener = _opener
            if slot == "direct-api" and base == env.get("EASEL_DIRECT_API_BASE_URL", "").strip().rstrip("/"):
                opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
            with opener.open(rq, timeout=15) as resp:
                return {"baseUrl": base, "ok": resp.status == 200, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            return {"baseUrl": base, "ok": False, "ms": int((time.time() - t0) * 1000),
                    "detail": f"{type(e).__name__}: {e}"[:140]}

    def _run_probes() -> list[dict]:
        out: list[dict] = []
        for base, key, is_anthropic, slot in targets:
            out.append(_probe(base, key, anthropic=is_anthropic, slot=slot))
        return out

    results = await asyncio.to_thread(_run_probes)
    return {"channel": channel, "results": results, "testedAt": int(time.time())}


class AttachmentRef(BaseModel):
    id: str
    name: str
    path: str


class ChatRequest(BaseModel):
    message: str
    persona: str | None = None
    sessionId: str | None = None
    turnId: str | None = None
    attachments: list[AttachmentRef] = Field(default_factory=list)


def _attachment_scope(session_id: str) -> str:
    """Map a browser session to a filesystem-safe, non-reversible inbox scope."""
    value = session_id.strip()
    if not value or len(value) > 256:
        raise HTTPException(400, "无效的会话标识")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _attachment_id(scope: str, path: str) -> str:
    return hashlib.sha256(f"{scope}\0{path}".encode("utf-8")).hexdigest()[:24]


def _attachment_context(req: ChatRequest) -> str:
    """Validate attachment ownership and build an Agent-only attachment manifest."""
    if not req.attachments:
        return ""
    if not req.sessionId:
        raise HTTPException(400, "附件必须绑定到会话")

    scope = _attachment_scope(req.sessionId)
    rows: list[str] = []
    seen: set[str] = set()
    for attachment in req.attachments:
        rel = Path(attachment.path)
        if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 4:
            raise HTTPException(400, "附件路径无效")
        if rel.parts[0] != "_inbox" or rel.parts[1] != scope:
            raise HTTPException(403, "附件不属于当前会话")
        normalized = rel.as_posix()
        if attachment.id != _attachment_id(scope, normalized):
            raise HTTPException(403, "附件标识校验失败")
        full = _safe_output_target(normalized)
        if not full.is_file():
            raise HTTPException(404, f"附件不存在：{attachment.name}")
        if normalized in seen:
            continue
        seen.add(normalized)
        rows.append(f"- outputs/{normalized}")

    return (
        "〔系统附件清单，仅供本轮执行，不要向用户复述文件上传过程或内部路径〕\n"
        "只允许使用下列当前会话附件；禁止扫描、枚举或猜测 outputs/_inbox 中的其他文件：\n"
        + "\n".join(rows)
        + "\n需要纳入内容项目时，将清单内文件复制到 outputs/<项目>/assets/ 后再使用；"
          "保留 inbox 原件，确保重试仍可复现。"
    )


def _chat_message(req: ChatRequest) -> str:
    context = _attachment_context(req)
    message = req.message.strip()
    if context:
        message = f"{message}\n\n{context}" if message else context
    if not message:
        raise HTTPException(400, "消息不能为空")
    return _publishing_message(chat_turn_message(message, req.persona))


def _direct_chat_content(req: ChatRequest) -> tuple[str | list, str]:
    _attachment_context(req)  # Reuse ownership/path validation before reading any attachment.
    message = req.message.strip()
    if not message and not req.attachments:
        raise HTTPException(400, "消息不能为空")
    system = SYSTEM_PROMPT
    if req.persona:
        if not _valid_persona_name(req.persona) or not profile_exists(req.persona):
            raise HTTPException(400, "画像不存在或名称无效")
        system += "\n\n当前画像：\n" + load_profile_text(req.persona)
    content = [{"type": "text", "text": message or "请分析这些附件"}]
    for attachment in req.attachments:
        path = _safe_output_target(attachment.path)
        if path.suffix.lower() in TEXT_EXTS:
            if path.stat().st_size > 65536:
                raise HTTPException(413, "API 直连模式文本附件不能超过 64 KiB")
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError:
                raise HTTPException(400, "文本附件需要 UTF-8 编码") from None
            content.append({"type": "text", "text": f"附件 {attachment.name}：\n{text}"})
        elif path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise HTTPException(413, "API 直连模式图片附件不能超过 4 MiB")
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        else:
            raise HTTPException(400, "API 直连模式支持文本和图片附件；此附件需要文件处理工具")
    return (content if req.attachments else message), system


# 每个会话（session-key）一把锁：防止同一会话被两个并发的对话进程同时处理。
# 并发跑同一 session 文件会触发引擎的会话接管错误（进程 rc=1、
# 表现为「答一半停在冒号」），以及会话串味（一个会话读到另一个的 session 文件内容）。
# 不同会话 key 不同锁 → 不同对话仍可并行；只序列化「同一会话」的重叠请求。
_session_locks: dict[str, asyncio.Lock] = {}


def _session_lock(sk: str) -> asyncio.Lock:
    lk = _session_locks.get(sk)
    if lk is None:
        lk = asyncio.Lock()
        _session_locks[sk] = lk
    return lk


def _session_flock_path(sk: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sk)[:120]
    return SESSIONS_DIR / f"{safe}.lock"


class _CrossProcLock:
    """跨进程会话锁（fcntl.flock）：同一会话同一时刻只允许一个进程在跑。

    现有 _session_lock（asyncio）只在单个 web 进程内串行；挡不住两个浏览器标签/cron 并发碰
    同一会话 → 并发写同一份 transcript 会崩溃（rc=1，答一半就停）。flock 在持有进程退出时
    自动释放，无 stale 死锁。返回 True=拿到锁，False=超时未拿到。
    """

    def __init__(self, sk: str):
        self._path = _session_flock_path(sk)
        self._fh = None
        self.acquired = False

    def acquire(self, timeout: float = 300.0, poll: float = 0.5) -> bool:
        try:
            SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "a+b")
            if os.name == "nt":
                self._fh.seek(0, os.SEEK_END)
                if self._fh.tell() == 0:
                    self._fh.write(b"0")
                    self._fh.flush()
        except OSError:
            return False  # 拿不到文件句柄就不强求（退化为仅 asyncio 锁）
        deadline = time.time() + timeout
        while True:
            try:
                if os.name == "nt":
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
                return True
            except OSError:
                if time.time() >= deadline:
                    return False
                time.sleep(poll)

    def release(self) -> None:
        if self._fh is not None:
            try:
                if self.acquired:
                    if os.name == "nt":
                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            self.acquired = False


def _turn_file(sk: str) -> Path:
    """每会话最近一轮结果的落盘路径（sk 做文件名安全化）。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sk)[:120]
    return SESSIONS_DIR / f"{safe}.json"


def _job_event_file(turn_id: str) -> Path:
    """Per-turn append-only event log used to resume SSE without restarting the agent."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", turn_id)[:160]
    return SESSIONS_DIR / "jobs" / f"{safe}.jsonl"


def _read_job_events(turn_id: str, after: int = 0) -> list[dict]:
    path = _job_event_file(turn_id)
    if not path.is_file():
        return []
    events = []
    try:
        # Read/decode each record separately: a concurrent append can end in
        # partial JSON or even half a UTF-8 character. Keep valid records and
        # retry the incomplete tail on the next read without changing the log.
        with path.open("rb") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                event_id = event.get("id")
                if (type(event_id) is not int or event_id <= 0
                        or not isinstance(event.get("event"), str)
                        or not event["event"]):
                    continue
                if event_id > after:
                    events.append(event)
    except OSError:
        return []
    return events



def _save_turn(sk: str, status: str, text: str, extra: dict | None = None) -> None:
    """持久化本轮结果（running/done），供 SSE 连接中断后前端用 /api/chat/last 取回。

    后端跑完整轮不依赖客户端连接——长任务时 webide 代理会掐断 SSE，但 run 仍跑到底，
    结果写这里，前端断线后轮询即可拿到完整回答（否则"运行完也不说一声"）。
    """
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"status": status, "text": text, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if extra:
            payload.update(extra)
        tmp = _turn_file(sk).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _turn_file(sk))
    except Exception:
        pass


# 后台 supervisor 任务集合：持有强引用防被 GC；每个对话流的 run 跑在这里，
# 与客户端 SSE 连接解耦（断线不杀 run）。
_BG_TASKS: set = set()
_DIRECT_CHAT_TASKS: set = set()

# 正在跑的对话进程（sk→proc），供用户**显式「停止」**终止；断线**不**经此路径（断线不杀）。
_RUNNING_CHAT: dict = {}
# 被用户显式停止的会话 key：supervisor 据此把本轮当作正常「已停止」收尾（不报「被中断」、释放会话锁）。
_STOPPED_CHAT: set = set()
_HARNESS_TASKS: set = set()
_HARNESS_QUESTIONS: dict = {}


class _HarnessProc(_GatewayHttpProc):
    def __init__(self) -> None:
        super().__init__()
        self._cancel_requested = False

    def terminate(self) -> None:
        if self._task is not None:
            task = self._task
            def cancel_once():
                if (not task.done() and not self._cancel_requested
                        and not getattr(task, "cancelling", lambda: 0)()):
                    self._cancel_requested = True
                    task.cancel()
            task.get_loop().call_soon_threadsafe(cancel_once)


async def _harness_ask(sk: str, questions: list[dict], emit) -> dict:
    identifier = "harness_" + uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    record = {"session": sk, "future": future, "status": "pending", "questions": questions}
    _HARNESS_QUESTIONS[identifier] = record
    try:
        emit("question", _scrub_secrets(json.dumps(
            {"id": identifier, "questions": questions}, ensure_ascii=False)))
        answers = await asyncio.wait_for(future, TIMEOUT_CHAT)
        record["status"] = "resolved"
        return answers
    finally:
        _HARNESS_QUESTIONS.pop(identifier, None)


async def _run_harness_chat(req: ChatRequest, sk: str, message: str, runtime: str,
                            config: dict, emit, timeout: int = TIMEOUT_CHAT, *, interactive=True) -> str:
    pk = f"web:{sk}"
    turn_id = req.turnId or uuid.uuid4().hex
    full_text: list[str] = []
    clean_end = False
    reason = "error"
    error_message = ""
    lock = _session_lock(sk)
    xlock = _CrossProcLock(sk)
    acquired = False
    claimed = False
    locked = False
    proc = _HarnessProc()
    task = asyncio.current_task()
    _HARNESS_TASKS.add(task)

    saw_output = [False]

    def receive(kind, text):
        safe = _scrub_secrets(text)
        if kind == "token":
            full_text.append(safe)
        saw_output[0] = True
        emit(kind, safe)

    async def ask(questions):
        if not interactive:
            raise HarnessError("此操作需要权限确认，请在流式对话中执行并确认权限")
        return await _harness_ask(sk, questions, receive)

    try:
        if lock.locked():
            emit("activity", "这个会话上一条还在运行，等待完成…")
        await lock.acquire()
        locked = True
        deadline = time.monotonic() + min(timeout, 300)
        while not acquired and time.monotonic() < deadline:
            acquired = xlock.acquire(0)
            if not acquired:
                xlock.release()
                await asyncio.sleep(0.2)
        if not acquired:
            raise HarnessError("此会话正在另一个窗口运行，请稍后再试")
        if _chat_backend(sk) != runtime:
            raise HarnessError("底座已切换，请使用新对话重试")
        record = harness_store.binding(SESSIONS_DIR, sk)
        native_id = record.get("native_id", "")
        harness_store.bind(SESSIONS_DIR, sk, runtime, native_id)
        _STOPPED_CHAT.discard(sk)
        proc._task = task
        _RUNNING_CHAT[sk] = proc
        _save_turn(pk, "running", "", {"turn_id": turn_id, "runtime": runtime})
        claimed = True
        env = {**read_settings(ENV_FILE), **_proxy_env(), "EASEL_AGENT_RUNTIME": runtime,
               "EASEL_ROOT": str(PROJECT_ROOT)}
        # 模型未显式配置时，按该底座的原生模型目录解析，避免落到引擎自带、
        # 本机不可达的默认模型（如 pi 默认的 claude/api.anthropic.com）。
        model = config["models"].get(runtime, "")
        if not model and runtime in ("pi", "codex", "opencode"):
            try:
                model = await asyncio.wait_for(
                    resolve_effective_model(runtime, "",
                        RunRequest("", PROJECT_ROOT, env, "", "", "", 25)), 25)
            except Exception:
                pass
        request = RunRequest(message, PROJECT_ROOT, env, native_id,
                             model, config["sandbox"], timeout,
                             session_key=sk, codex_auto_approve=config.get("codex_auto_approve", False))
        await asyncio.wait_for(harnesses.adapter(runtime).run(
            request, receive, ask,
            lambda identifier: harness_store.bind(SESSIONS_DIR, sk, runtime, identifier)), timeout)
        clean_end = True
        reason = "completed"
    except asyncio.CancelledError:
        reason = "stopped" if sk in _STOPPED_CHAT else "shutdown"
        emit("activity", "已停止" if reason == "stopped" else "服务关闭，本轮已取消")
    except asyncio.TimeoutError:
        reason = "timeout"
        error_message = "Agent 执行超时，已终止本轮进程"
        emit("error", error_message)
    except (HarnessError, HTTPException) as error:
        error_message = _scrub_secrets(str(error.detail if isinstance(error, HTTPException) else error))
        emit("error", error_message)
    except Exception:
        error_message = "Agent 执行失败，请检查本机 CLI、登录和模型配置"
        emit("error", error_message)
    finally:
        if claimed:
            snapshot = "".join(full_text) + (f"\n\n❌ {error_message}" if error_message else "")
            _save_turn(pk, "done", snapshot, {
                "turn_id": turn_id, "runtime": runtime,
                "clean_end": clean_end, "stop_reason": reason})
        # 注意：claim 失败（锁被占/底座已切换）时刻意**不**落盘——保留上一轮的
        # 结果快照不被失败轮覆盖（见 test_failed_ownership_check_does_not_overwrite_
        # previous_result）；错误经 SSE 事件流（job log）回放可见。
        proc.finish()
        if _RUNNING_CHAT.get(sk) is proc:
            _RUNNING_CHAT.pop(sk, None)
        if claimed:
            _STOPPED_CHAT.discard(sk)
        xlock.release()
        if locked:
            lock.release()
        _HARNESS_TASKS.discard(task)
    # 干净结束但 Agent 全程零输出（无 token/思考/工具/报错）：不要让它停在
    # 「正在唤醒 agent…」像卡死一样——明确提示并引导重试（常见于模型端瞬时无响应/过载）。
    if clean_end and not error_message and not saw_output[0]:
        emit("error", "Agent 本轮没有返回内容（模型可能无响应或超时），请重试或稍后再试")
    return "".join(full_text)


async def _run_direct_chat(req: ChatRequest, sk: str, content, system: str, emit) -> str:
    """Run one API turn with the same stop/recovery/session locking as Gateway chat."""
    pk = f"web:{sk}"
    turn_id = req.turnId or uuid.uuid4().hex
    full_text = []
    clean_end = False
    reason = "error"
    error_message = ""
    lock = _session_lock(sk)
    xlock = _CrossProcLock(sk)
    proc = _GatewayHttpProc()
    acquired = False
    claimed = False
    locked = False
    settings = read_settings(ENV_FILE)
    task = asyncio.current_task()
    _DIRECT_CHAT_TASKS.add(task)
    if lock.locked():
        emit("activity", "⏳ 这个会话上一条还在跑，排队等它结束…")
    try:
        await lock.acquire()
        locked = True
        deadline = time.monotonic() + min(TIMEOUT_CHAT, 300)
        while True:
            acquired = xlock.acquire(0)
            if acquired:
                break
            xlock.release()
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.2)
        if not acquired:
            raise DirectAPIError("这个会话正在另一个窗口运行，请稍候再试")
        _save_turn(pk, "running", "", {"turn_id": turn_id})
        claimed = True
        proc._task = asyncio.current_task()
        _RUNNING_CHAT[sk] = proc
        emit("activity", "🧠 正在通过 API 思考…")

        async def consume():
            nonlocal clean_end, reason
            async for kind, text in stream_chat(
                    settings, content, system=system,
                    history_file=history_path(SESSIONS_DIR, sk), timeout=TIMEOUT_CHAT):
                if kind == "finish":
                    clean_end, reason = True, text
                else:
                    if kind == "token":
                        full_text.append(text)
                    emit(kind, text)

        await asyncio.wait_for(consume(), timeout=TIMEOUT_CHAT)
    except asyncio.CancelledError:
        reason = "user_stopped" if sk in _STOPPED_CHAT else "cancelled"
        if reason != "user_stopped":
            raise
    except (DirectAPIError, asyncio.TimeoutError) as error:
        error_message = str(error) or "API 请求超时"
        emit("error", error_message)
    except OSError:
        error_message = "会话历史保存失败，请检查项目 outputs 目录权限"
        emit("error", error_message)
    finally:
        proc.finish()
        snapshot = "".join(full_text) + (f"\n\n❌ {error_message}" if error_message else "")
        try:
            if claimed:
                _save_turn(pk, "done", snapshot, {
                    "turn_id": turn_id, "clean_end": clean_end, "stop_reason": reason,
                })
        finally:
            if _RUNNING_CHAT.get(sk) is proc:
                _RUNNING_CHAT.pop(sk, None)
            if claimed:
                _STOPPED_CHAT.discard(sk)
            xlock.release()
            if locked:
                lock.release()
            _DIRECT_CHAT_TASKS.discard(task)
    return "".join(full_text)


@app.get("/api/chat/last/{session_id}")
async def api_chat_last(session_id: str, turn_id: str | None = None, persona: str | None = None):
    """取某会话最近一轮的完整结果（SSE 断线后前端据此取回，避免丢结果）。"""
    sk = _eff_sk(session_id, persona)
    f = _turn_file(f"web:{sk}")
    if not f.is_file():
        return {"status": "none", "text": ""}
    try:
        payload = json.loads(f.read_text(encoding="utf-8"))
        if turn_id and payload.get("turn_id") != turn_id:
            return {"status": "stale", "text": "", "turn_id": payload.get("turn_id")}
        return payload
    except Exception:
        return {"status": "none", "text": ""}


@app.get("/api/chat/jobs/{turn_id}/stream")
async def api_chat_job_stream(turn_id: str, after: int = 0):
    """Replay missed events, then tail this turn until its terminal event arrives."""
    # A stale browser-side pendingTurnId must fail promptly instead of receiving
    # heartbeats forever. The frontend can then recover from the final snapshot.
    if not _job_event_file(turn_id).is_file():
        raise HTTPException(404, "对话任务记录不存在或已失效")

    async def events():
        cursor = max(0, after)
        idle_since = time.monotonic()
        while True:
            batch = _read_job_events(turn_id, cursor)
            if batch:
                idle_since = time.monotonic()
                for event in batch:
                    cursor = int(event["id"])
                    raw = event.get("data")
                    # job 日志里 question 的 data 已是序列化 JSON 字符串（实时流
                    # forward() 对 question 原样透传），这里若再 json.dumps 会双层
                    # 转义 → 前端 JSON.parse 拿到字符串而非对象 → ask_user 选项
                    # 卡片渲染成空壳。其余事件存原文/dict，与实时流的 dumps 规则
                    # 一致。因此仅 question 按实时流规则原样透传。
                    if event["event"] == "question" and isinstance(raw, str):
                        data_out = raw
                    else:
                        data_out = json.dumps(raw, ensure_ascii=False)
                    yield {
                        "id": str(cursor),
                        "event": event["event"],
                        "data": data_out,
                    }
                    if event["event"] in ("done", "error"):
                        return
            else:
                # Keep proxy connections active; reconnecting remains safe if it still drops.
                if time.monotonic() - idle_since >= 15:
                    yield {"event": "ping", "data": "{}"}
                    idle_since = time.monotonic()
                await asyncio.sleep(0.25)

    return EventSourceResponse(events(), headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Content-Encoding": "identity",
    })


@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest):
    """SSE 真流式对话：api 直连走 _run_direct_chat（纯聊天），其余底座走 _run_harness_chat（Agent）。

    run 跑在独立后台任务里：客户端断开只结束转发、不取消 run，跑到底后落盘，前端断线后
    用 /api/chat/last 取回完整结果。
    """
    runtime = _chat_backend(req.sessionId, req.persona)
    sk = _eff_sk(req.sessionId, req.persona)
    config = _harness_settings()
    direct = runtime == "api"
    if direct:
        content, system = _direct_chat_content(req)
        message = ""
    else:
        if sk and history_path(SESSIONS_DIR, sk).is_file():
            raise HTTPException(409, "此会话属于 API 直连，请新建会话使用当前底座")
        message = _chat_message(req)

    # supervisor（跑 run）与 forward（转发 SSE 给浏览器）之间的事件通道。
    # 关键：run 跑在独立后台任务里，客户端断开只结束 forward，不取消 supervisor →
    # run 照常跑到底、结果落盘，前端断线后 /api/chat/last 取回。
    loop = asyncio.get_event_loop()
    client_q: asyncio.Queue = asyncio.Queue()
    CLIENT_DONE = object()

    async def supervisor():
        pk = f"web:{sk}"                 # 落盘 key（与 /api/chat/last 一致）
        turn_id = req.turnId or uuid.uuid4().hex
        event_seq = 0
        full_text: list[str] = []        # 累积完整回答，供断线取回
        timed_out = False                # 只有真·超时才 terminate 进程；断线绝不杀

        event_path = _job_event_file(turn_id)
        try:
            event_path.parent.mkdir(parents=True, exist_ok=True)
            event_path.write_text("", encoding="utf-8")
        except OSError:
            pass

        def to_client(kind, text=None, **extra):
            nonlocal event_seq
            event_seq += 1
            data = ({"sessionKey": extra.get("sessionKey")} if kind == "done" else text)
            event = {"id": event_seq, "event": kind, "data": data}
            try:
                with event_path.open("a", encoding="utf-8") as ef:
                    ef.write(json.dumps(event, ensure_ascii=False) + "\n")
                    ef.flush()
            except OSError:
                pass
            client_q.put_nowait({"t": kind, "text": text, "id": event_seq, **extra})

        # 秒级反馈：发出即亮「已收到」，不等 agent 冷启动（首个 SSE 事件，随流回放必达）
        to_client("activity", "⏳ 已收到…" if direct else "⏳ 已收到，正在唤醒 agent…")

        if direct:
            try:
                await _run_direct_chat(
                    req.model_copy(update={"turnId": turn_id}), sk, content, system, to_client)
            finally:
                to_client("done", sessionKey=sk)
                client_q.put_nowait(CLIENT_DONE)
            return

        try:
            await _run_harness_chat(req.model_copy(update={"turnId": turn_id}),
                                    sk, message, runtime, config, to_client)
        finally:
            to_client("done", sessionKey=sk)
            client_q.put_nowait(CLIENT_DONE)
        return

    # 把 run 跑在独立后台任务里（持强引用防 GC）——客户端断开不取消它。
    task = asyncio.create_task(supervisor())
    _BG_TASKS.add(task)

    def _bg_done(t):
        _BG_TASKS.discard(t)
        if t.cancelled():
            client_q.put_nowait(CLIENT_DONE)
            return
        try:
            exc = t.exception()   # 取出异常避免「never retrieved」告警
        except Exception:
            exc = None
        if exc is not None:
            # supervisor 意外崩溃：解锁 forward，别让它空等
            try:
                client_q.put_nowait(CLIENT_DONE)
            except Exception:
                pass
    task.add_done_callback(_bg_done)

    async def forward():
        """纯转发：从 client_q 取事件 yield 给浏览器。

        客户端断开（关标签/代理掐断）只会结束本生成器，supervisor 任务不受影响，
        继续把 run 跑完并落盘 → 前端断线后 /api/chat/last 取回完整结果。
        """
        idle_since = time.monotonic()
        while True:
            try:
                item = await asyncio.wait_for(client_q.get(), timeout=10)
            except asyncio.TimeoutError:
                # 长时间无输出（等模型长回复 / 制作类长任务）→ 发心跳，让用户知道没卡死。
                # 用独立的 `heartbeat` 事件，不走 `activity`：否则会覆盖掉真实的
                # 「🧠 正在思考…/🛠️ 制作中…」状态与思考流（防呆消息把思考设计顶掉的根因）。
                # 发出后重置 idle_since → 心跳按 30s 一次的节奏，不再每 10s 重复刷屏。
                if time.monotonic() - idle_since >= 30:
                    idle_since = time.monotonic()
                    yield {"event": "heartbeat", "data": json.dumps(
                        "仍在处理中，未卡住…（复杂或制作类任务会花点时间）", ensure_ascii=False)}
                continue
            if item is CLIENT_DONE:
                break
            idle_since = time.monotonic()
            t = item["t"]
            if t == "token":
                yield {"id": str(item["id"]), "event": "token", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "thinking":
                yield {"id": str(item["id"]), "event": "thinking", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "activity":
                yield {"id": str(item["id"]), "event": "activity", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "question":
                yield {"id": str(item["id"]), "event": "question", "data": item["text"]}
            elif t == "error":
                yield {"id": str(item["id"]), "event": "error", "data": json.dumps(item["text"], ensure_ascii=False)}
            elif t == "done":
                yield {"id": str(item["id"]), "event": "done", "data": json.dumps({"sessionKey": item.get("sessionKey")}, ensure_ascii=False)}

    return EventSourceResponse(forward(), headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Content-Encoding": "identity"})


class QuestionAnswerRequest(BaseModel):
    sessionId: str | None = None
    questionId: str
    answers: dict  # {questionId: [optionValues]}
    resolvedBy: str | None = None


@app.post("/api/chat/question/answer")
async def api_question_answer(req: QuestionAnswerRequest):
    """前端点击 ask_user 选项后调用：把答案回送给等待的 Agent 底座问答。"""
    if req.questionId.startswith("harness_"):
        record = _HARNESS_QUESTIONS.get(req.questionId)
        if record is None:
            return {"ok": False, "error": "权限或问答请求已过期，请重试"}
        required = {question["questionId"] for question in record["questions"]}
        if set(req.answers or {}) != required:
            raise HTTPException(400, "回答与当前问题不匹配")
        if record["future"].done():
            return {"ok": False, "error": "请求已回答"}
        record["future"].set_result(req.answers)
        return {"ok": True}
    return {"ok": False, "error": "问答请求已过期或不属于当前底座，请重试"}


class QuestionStatusRequest(BaseModel):
    questionIds: list[str]


@app.post("/api/chat/question/status")
async def api_question_status(req: QuestionStatusRequest):
    """批量查 question 状态（重放旧事件时过滤已解决的题）。"""
    out = {}
    for identifier in (req.questionIds or []):
        if identifier.startswith("harness_"):
            out[identifier] = {"status": _HARNESS_QUESTIONS.get(identifier, {}).get("status", "not_found")}
        else:
            out[identifier] = {"status": "not_found"}
    return {"ok": True, "questions": out}


class StopRequest(BaseModel):
    sessionId: str | None = None
    persona: str | None = None


@app.post("/api/chat/stop")
async def api_chat_stop(req: StopRequest):
    """用户显式停止当前会话正在跑的对话 agent：终止进程 → supervisor 收尾释放会话锁 →
    下一句立刻能发（不再卡「上一条还在跑」）。仅此显式入口会杀进程；客户端断线不经此路径。"""
    base = (req.sessionId or "").strip()
    sk = _eff_sk(base, req.persona) if base else ""
    proc = _RUNNING_CHAT.get(sk) if sk else None
    if proc is not None and proc.poll() is None:
        _STOPPED_CHAT.add(sk)          # 标记为用户停止，供 supervisor 正常收尾（不报「被中断」）
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            await asyncio.to_thread(proc.wait, timeout=3)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        # supervisor removes the running marker only after persisting the final
        # snapshot and releasing both session locks.
        deadline = time.monotonic() + 5
        while _RUNNING_CHAT.get(sk) is proc and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return {"stopped": True}
    return {"stopped": False}          # 没有在跑（可能已结束）→ 前端照常清理即可


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """非流式对话（备选）。"""
    runtime = _chat_backend(req.sessionId, req.persona)
    sk = _eff_sk(req.sessionId, req.persona)
    def emit(kind, text):
        errors.append(text) if kind == "error" else None
    if runtime == "api":
        content, system = _direct_chat_content(req)
        errors = []
        result = await _run_direct_chat(req, sk, content, system, emit)
        if errors:
            raise HTTPException(502, errors[0])
        return {"response": result, "sessionKey": sk}
    message = _chat_message(req)
    errors = []
    result = await _run_harness_chat(req, sk, message, runtime, _harness_settings(), emit,
                                     interactive=False)
    if errors:
        raise HTTPException(502, errors[0])
    return {"response": result, "sessionKey": sk}


class SkillRequest(BaseModel):
    skill: str
    input: str
    persona: str | None = None


@app.post("/api/skill")
async def api_skill(req: SkillRequest):
    if _direct_api_enabled():
        raise HTTPException(409, "API 直连模式支持聊天；SKILL 执行需要具备本机工具的 Agent 运行时")
    skill_full = find_skill(req.skill)
    if skill_full is None:
        raise HTTPException(404, f"SKILL '{req.skill}' 不存在")
    if not _skill_in_scope(skill_full, _persona_skill_scope(req.persona)):
        raise HTTPException(404, f"SKILL '{req.skill}' 不在当前画像的技能范围")
    message = f"{_persona_prefix(req.persona)}请执行 /{skill_full}，内容如下：\n\n{req.input}"
    # 统一给足超时：制作类 SKILL（生视频/多镜合成）可能跑很久，取安全上界
    timeout = TIMEOUT_PRODUCE
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_agent_sync, message, timeout)
    return {"response": result}


@app.get("/api/outputs")
def api_outputs(persona: str | None = None):
    # 产物树是全量递归扫描（文件量大时单次可达数十秒）。必须是同步 handler：
    # FastAPI 会自动放入线程池执行；若写成 async def 直调，扫描期间会阻塞事件循环，
    # 全站所有请求（含 /api/status）一起挂起等它。
    return get_output_tree(persona)


@app.get("/api/output-children/{path:path}")
def api_output_children(path: str, persona: str | None = None):
    """懒加载外部内容源（本地知识库）某层子节点。知识库可能上万文件，不全量入树，
    按需逐层展开（只读；路径经 _resolve_external 防穿越，绝不触碰 outputs/ 写操作）。
    persona 有聚焦时按 kb_scopes.json 过滤：只展开命中画像范围的目录/文件，
    使同一知识库在不同画像下查询结果不同。"""
    ext = _resolve_external(path)
    if ext is None:
        raise HTTPException(404, "路径不在外部内容源")
    root, target = ext
    patterns = _persona_kb_scope(persona)
    rel = _external_rel(path)
    if patterns and not _kb_in_scope(rel, patterns):
        return []   # 当前目录整体在画像聚焦之外，不展开
    if not target.is_dir():
        return []
    children = []
    try:
        entries = sorted(target.iterdir())
    except OSError:
        return []
    for c in entries:
        if c.name.startswith("."):
            continue
        child_rel = f"{rel}/{c.name}" if rel else c.name
        if patterns and not _kb_in_scope(child_rel, patterns):
            continue
        child_path = f"{path}/{c.name}"
        if c.is_dir():
            children.append({
                "name": c.name, "type": "dir", "path": child_path,
                "external": True, "mtime": _dir_mtime(c), "fileCount": _count_files(c),
            })
        else:
            fm = _file_meta(c, child_path)
            fm["type"] = "file"
            fm["external"] = True
            children.append(fm)
    # 目录在前、文件在后；同级按 mtime 倒序（与内容库内部排序一致）
    children.sort(key=lambda x: (x["type"] == "file", -(x.get("mtime") or 0)))
    return children


@app.get("/api/persona/{name}/kb-scope")
async def api_persona_kb_scope(name: str):
    """某画像对知识库（外部内容源）的聚焦范围；前端用于「内容库」显示当前查询视角。
    无聚焦 → focused=false（通用，全量）。"""
    patterns = _persona_kb_scope(name)
    return {"focused": bool(patterns), "patterns": patterns}


# ── 知识库能力：查询 / 沉淀（集成项目 Otter Wiki llm-wiki；查询随画像聚焦）──
@app.get("/api/kb/status")
def api_kb_status(persona: str | None = None):
    """知识库索引规模 + 可用性（llm-wiki 是否就绪、vault 路径）。"""
    return kb.status()


@app.get("/api/kb/query")
def api_kb_query(question: str = "", limit: int = 10, persona: str | None = None):
    """BM25 证据包（结构化），按画像 kb_scopes.json 聚焦；通用/无画像 = 全量。
    返回 documents[] 含 relative_path / best_score / hits[]（heading/line/snippet/hash），
    前端据此展示命中并可点开 @源/relative_path 原文。"""
    return kb.query(question, limit=limit, persona=persona)


@app.get("/api/kb/ask")
def api_kb_ask(question: str = "", limit: int = 10, persona: str | None = None):
    """cite-ready 证据摘要（文本）；综合结论由宿主完成（工具不生成答案、不写库）。"""
    return kb.ask(question, limit=limit, persona=persona)


@app.get("/api/kb/search")
def api_kb_search(query: str = "", limit: int = 10, persona: str | None = None):
    """词法检索（--json），按 relative_path 随画像聚焦。"""
    return kb.search(query, limit=limit, persona=persona)


class KbIngestRequest(BaseModel):
    source: str = ""
    target: str | None = None
    title: str | None = None
    apply: bool = False


@app.post("/api/kb/ingest")
def api_kb_ingest(req: KbIngestRequest, persona: str | None = None):
    """把 URL/本地文件沉淀为收件箱笔记。dry-run 默认（返回 target/plan_sha256/预览）；
    apply=true 才写库（create-only 事务，同名冲突 fail-closed，绝不覆盖）。"""
    return kb.ingest(req.source, target=req.target, title=req.title, apply=req.apply, persona=persona)


class KbSaveRequest(BaseModel):
    title: str = ""
    content: str = ""
    target: str | None = None
    apply: bool = False


@app.post("/api/kb/save")
def api_kb_save(req: KbSaveRequest, persona: str | None = None):
    """把显式内容（对话结论/草稿）沉淀为笔记。dry-run 默认；apply=true 才写库。"""
    return kb.save(req.title, content=req.content, target=req.target, apply=req.apply, persona=persona)


@app.get("/api/artifacts/archive")
def api_artifact_archive(path: str):
    full = _safe_output_path(path)
    if full.suffix.lower() != '.zip':
        raise HTTPException(400, '仅支持 ZIP 产物索引')
    artifacts = []
    seen = set()
    extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.avif', '.bmp',
                  '.ico', '.html', '.htm', '.mp4', '.webm', '.mov', '.m4v',
                  '.mp3', '.wav', '.ogg', '.m4a', '.flac', '.pdf'}
    try:
        with zipfile.ZipFile(full) as archive:
            entries = archive.infolist()
            if len(entries) > 1000:
                raise HTTPException(400, '压缩包文件过多，暂不自动预览')
            for entry in entries:
                name = entry.filename
                parts = name.split('/')
                if (entry.is_dir() or name.startswith('/') or '\\' in name
                        or any(not part or part.startswith(('.', '_')) for part in parts)
                        or any(ord(character) < 32 for character in name)
                        or Path(name).suffix.lower() not in extensions):
                    continue
                relative = (full.parent / name).relative_to(OUTPUTS_DIR.resolve()).as_posix()
                try:
                    original = _safe_output_path(relative)
                except HTTPException:
                    continue
                if original.stat().st_size != entry.file_size or relative in seen:
                    continue
                seen.add(relative)
                artifacts.append(relative)
                if len(artifacts) >= 100:
                    break
    except (OSError, zipfile.BadZipFile):
        raise HTTPException(400, '无法读取压缩包索引')
    return {'paths': artifacts}


@app.get("/api/output/{path:path}")
async def api_output(path: str):
    """文本产物内容。二进制/媒体返回 isBinary=true，前端改用 /api/media。"""
    full = _safe_output_path(path)
    kind = _file_kind(full.name)
    if kind not in ("text",):
        return {"path": path, "content": "", "kind": kind, "isBinary": True}
    try:
        return {"path": path, "content": full.read_text(encoding="utf-8"), "kind": "text", "isBinary": False}
    except UnicodeDecodeError:
        return {"path": path, "content": "", "kind": "binary", "isBinary": True}


@app.get("/api/media/{path:path}")
async def api_media(path: str):
    """原样输出媒体文件（图片/视频/音频/HTML/PDF），供 <img>/<video>/iframe/下载。"""
    full = _safe_output_path(path)
    # HTML 预览会在本地被就地重新生成（如 gzh-design 重排/内联图片），必须禁缓存，
    # 否则浏览器/代理按启发式缓存旧版 → 内容库 iframe 打开的是过期预览（复制粘贴带旧图 URL）。
    headers = ({"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}
               if full.suffix.lower() in {".html", ".htm"} else {})
    return FileResponse(full, headers=headers)


# 系统数据目录/文件——不允许从内容库删除（删了会丢登录态/日历/发布记录）
PROTECTED_OUTPUTS = {"_login", "_analytics", "_schedule.json", "_ideas.json",
                     "_publish", "_publish.log", "_sessions", "_profile_build", "_debug", "_inbox"}
UPLOAD_EXTS = IMAGE_EXTS | VIDEO_EXTS | {
    ".pdf", ".txt", ".md", ".markdown", ".csv", ".json", ".srt", ".vtt",
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".mp3", ".wav", ".m4a"}
MAX_UPLOAD_MB = 50


def _unique_upload_path(dest: Path, filename: str) -> Path:
    """同一上传批次内保留所有同名文件，不让后一个静默覆盖前一个。"""
    target = dest / filename
    if not target.exists():
        return target
    source = Path(filename)
    index = 2
    while True:
        target = dest / f"{source.stem} ({index}){source.suffix}"
        if not target.exists():
            return target
        index += 1


def _safe_output_target(rel: str, *, must_exist: bool = True) -> Path:
    """解析到 outputs/ 内的文件或目录（防穿越）。与 _safe_output_path 不同：允许目录、
    可要求不必已存在（上传新文件时）。永远拒绝 outputs/ 根本身。"""
    full = (OUTPUTS_DIR / rel).resolve()
    root = OUTPUTS_DIR.resolve()
    if full == root or root not in full.parents:
        raise HTTPException(403, '非法路径')
    if must_exist and not full.exists():
        raise HTTPException(404, '不存在')
    return full


def _is_protected(full: Path) -> bool:
    """路径的顶层段是否属于受保护的系统项。"""
    try:
        rel = full.relative_to(OUTPUTS_DIR.resolve())
    except ValueError:
        return True
    if not rel.parts:
        return True
    first = rel.parts[0]
    return (
        first.startswith('_')
        or first.startswith('.')
        or first in PROTECTED_OUTPUTS
        or first.lower() in {'analytics', 'wechat'}
    )


@app.delete("/api/output/{path:path}")
async def api_output_delete(path: str):
    """删除内容库里的单个文件或整个项目目录。系统数据（_login/_analytics/日历/发布记录）受保护。"""
    if _resolve_external(path) is not None:
        raise HTTPException(403, "外部知识源为只读，不可删除")
    full = _safe_output_target(path)
    if _is_protected(full):
        raise HTTPException(403, '系统数据受保护，不可从内容库删除')
    is_dir = full.is_dir()
    try:
        if is_dir:
            shutil.rmtree(full)
        else:
            full.unlink()
    except OSError as e:
        raise HTTPException(500, f'删除失败：{e}')
    return {"ok": True, "deleted": path, "kind": "dir" if is_dir else "file"}


@app.post("/api/upload")
async def api_upload(
    files: list[UploadFile] = File(...),
    sessionId: str = Form(...),
):
    """Store chat attachments in a session-scoped inbox and return opaque refs."""
    scope = _attachment_scope(sessionId)
    batch = time.strftime('%Y%m%d-') + uuid.uuid4().hex[:6]
    dest = OUTPUTS_DIR / "_inbox" / scope / batch
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXTS:
            raise HTTPException(400, f'不支持的文件类型：{ext or name}')
        # 分块流式落盘 + 边写边限：与 /api/upload/local 同样模式，避免先整读进内存
        # （超限的多 GB 文件会把 web 进程撑爆）才报 413。
        cap = MAX_UPLOAD_MB * 1024 * 1024
        target = _unique_upload_path(dest, name)
        size = 0
        try:
            with open(target, 'wb') as out:
                while True:
                    chunk = await f.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(413, f'{name} 超过 {MAX_UPLOAD_MB}MB 上限')
                    out.write(chunk)
        except HTTPException:
            try:
                target.unlink()
            except OSError:
                pass
            raise
        rel = f"_inbox/{scope}/{batch}/{target.name}"
        saved.append({"id": _attachment_id(scope, rel), "name": target.name, "path": rel})
    if not saved:
        raise HTTPException(400, '没有文件')
    return {"ok": True, "files": saved}


@app.get("/api/upload/limits")
async def api_upload_limits():
    """当前上传上限（MB）——前端在文件超限时据此切换到本地复制通道。"""
    return {"ok": True, "max_mb": MAX_UPLOAD_MB}


@app.post("/api/upload/local")
async def api_upload_local(
    files: list[UploadFile] = File(...),
    sessionId: str = Form(...),
):
    """超过上传上限的文件复制通道：1MB 分块流式落盘（不整读进内存）、无大小上限；
    产出与 /api/upload 同构的附件引用（id/name/path），附件校验与清单链路零改动。"""
    scope = _attachment_scope(sessionId)
    batch = time.strftime('%Y%m%d-') + uuid.uuid4().hex[:6]
    dest = OUTPUTS_DIR / "_inbox" / scope / batch
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in UPLOAD_EXTS:
            raise HTTPException(400, f'不支持的文件类型：{ext or name}')
        target = _unique_upload_path(dest, name)
        with open(target, 'wb') as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        rel = f"_inbox/{scope}/{batch}/{target.name}"
        saved.append({"id": _attachment_id(scope, rel), "name": target.name, "path": rel})
    if not saved:
        raise HTTPException(400, '没有文件')
    return {"ok": True, "files": saved}


def _write_login_marker(platform: str, state: str, message: str = '', persona: str = '') -> None:
    """回写登录标记 outputs/_login[/<画像>]/<平台>.json（与 login_state.write_status 同格式，原子写）。
    whoami 真校验确认已登录后调用 → _account_logged_in 的快速路径此后自愈并持久。按画像隔离。"""
    d = _login_dir(persona)
    d.mkdir(parents=True, exist_ok=True)
    data = {"state": state, "message": message, "qr": "", "ts": int(time.time())}
    st = d / f'{platform}.json'
    tmp = st.with_suffix('.json.tmp')
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, st)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _account_logged_in(platform: str, cfg: dict, persona: str = '') -> bool:
    """尽力判断某平台（当前画像）是否已登录。按画像隔离登录态。
    浏览器平台的登录态只有启动浏览器才真能知道（profile 里总有 Cookies 文件，存在≠已登录，
    会误报），故这里只信「本流程最近一次登录成功」——即 status.json == success。
    biliup 的 cookie 文件只有登录成功才生成，可直接判。"""
    backend = cfg['backend']
    if backend == 'unsupported':
        return False
    if backend == 'biliup':
        return _persona_cookie_file(persona).is_file()
    if backend == 'wechat-oa':
        # 发布+数据都走「后台会话」→ 以 mp 后台登录成功为准；AppID 凭证作为兜底（旧配置）
        try:
            if _mp_login_status(persona).get('state') == 'success':
                return True
        except Exception:
            pass
        return _wechat_has_credentials()
    st = _login_dir(persona) / f'{platform}.json'
    if st.is_file():
        try:
            return json.loads(st.read_text(encoding="utf-8")).get('state') == 'success'
        except Exception:
            return False
    return False


def _login_status(platform: str, persona: str = '') -> dict:
    """读登录状态文件 + 二维码是否就绪（按画像隔离）。"""
    d = _login_dir(persona)
    st = d / f'{platform}.json'
    data = {'state': 'unknown', 'message': ''}
    if st.is_file():
        try:
            dd = json.loads(st.read_text(encoding="utf-8"))
            data = {'state': dd.get('state', 'unknown'), 'message': dd.get('message', '')}
        except Exception:
            pass
    # A runner that exits before writing its status must become an actionable error,
    # never the ambiguous ``unknown`` state shown as an endless spinner in the UI.
    proc = LOGIN_PROCESSES.get(_pk(persona, platform))
    if data['state'] not in ('success', 'expired', 'error') and proc is not None:
        code = proc.poll()
        if code is not None:
            data = {'state': 'error', 'message': f'登录程序异常退出（退出码 {code}），请查看 {_login_dir_rel(persona)}/{platform}.log'}
    qr = d / f'{platform}.png'
    if data['state'] in ('qr_ready', 'window_login') and qr.is_file():
        data['qr'] = f'{_login_dir_rel(persona)}/{platform}.png'
        try:
            data['qrTs'] = qr.stat().st_mtime_ns // 1_000_000
        except OSError:
            data['qrTs'] = 0
    else:
        data['qr'] = ''
        data['qrTs'] = 0
    return data


@app.get("/api/accounts")
async def api_accounts(persona: str = ''):
    return [
        {'platform': pf, 'name': cfg['name'], 'backend': cfg['backend'],
         'supported': cfg['backend'] != 'unsupported',
         'loggedIn': _account_logged_in(pf, cfg, persona),
         'note': cfg.get('note', '')}
        for pf, cfg in LOGIN_RUNNERS.items()
    ]


@app.post("/api/login/{platform}")
async def api_login_start(platform: str, persona: str = '', restart: bool = False):
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    lock = _LOGIN_START_LOCKS.setdefault(_pk(persona, platform), asyncio.Lock())
    async with lock:
        return await _start_login(platform, persona, restart)


async def _start_login(platform: str, persona: str = '', restart: bool = False):
    """启动后台登录并立即返回，二维码由前端状态轮询获取。按画像隔离登录态 + cookie/profile。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    backend = cfg['backend']
    if backend == 'unsupported':
        raise HTTPException(400, f"{cfg['name']} 暂不可用：{cfg.get('note', '')}")
    if backend == 'wechat-oa':
        # 公众号不走扫码：前端应改用凭证表单提交到 /api/accounts/{platform}/credentials。
        return {'mode': 'credentials', 'configured': _wechat_has_credentials(),
                'message': '微信公众号请填写 AppID / AppSecret'}
    key = _pk(persona, platform)
    existing = LOGIN_PROCESSES.get(key)
    if existing is not None and existing.poll() is None:
        if not restart:
            return {'mode': 'qr', **_login_status(platform, persona)}
        try:
            existing.terminate()
            await asyncio.to_thread(existing.wait, timeout=8)
        except subprocess.TimeoutExpired:
            raise HTTPException(409, '上一次登录仍在退出，请稍后重试') from None
        LOGIN_PROCESSES.pop(key, None)
    d = _login_dir(persona)
    d.mkdir(parents=True, exist_ok=True)
    qr = d / f'{platform}.png'
    status = d / f'{platform}.json'
    for f in (qr, status):
        try:
            f.unlink()
        except OSError:
            pass
    profile_base = str(_persona_profile_base(persona))
    if backend == 'xhs':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'xhs_publish.py'), 'login', '--no-proxy',
               '--profile-base', profile_base,
               '--qr-out', str(qr), '--status-file', str(status), '--timeout', str(LOGIN_TIMEOUT)]
    elif backend == 'biliup':
        # B站：TV 端扫码登录 API 生成二维码 + 写 biliup cookie（biliup login 需真终端，前端用不了）
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'bili_login.py'), 'login',
               '--qr-out', str(qr), '--status-file', str(status),
               '--cookie', str(_persona_cookie_file(persona)), '--timeout', str(LOGIN_TIMEOUT)]
    elif backend == 'douyin':
        code_file = d / f'{platform}.code'
        try:
            code_file.unlink()
        except OSError:
            pass
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'douyin_publish.py'), 'login',
               '--no-proxy', '--profile-base', profile_base,
               '--qr-out', str(qr), '--status-file', str(status),
               '--sms-code-file', str(code_file), '--timeout', str(LOGIN_TIMEOUT)]
    else:
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'web_publisher.py'), 'login-qr',
               '--platform', cfg['wp'], '--profile-base', profile_base,
               '--qr-out', str(qr), '--status-file', str(status),
               '--timeout', str(LOGIN_TIMEOUT)]
    # 新登录开始 → 清掉旧的 whoami 缓存（登录前可能缓存了「未登录」），避免登录成功后仍读到旧结果
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE.pop(key, None)
    log_path = d / f'{platform}.log'
    log_file = log_path.open('a', encoding='utf-8')
    login_env = _proxy_env()
    login_env['PYTHONIOENCODING'] = 'utf-8'
    login_env['PYTHONUTF8'] = '1'
    try:
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=login_env,
                                stdout=log_file, stderr=subprocess.STDOUT)
    finally:
        log_file.close()
    LOGIN_PROCESSES[key] = proc
    st = _login_status(platform, persona)
    if st['state'] == 'unknown':
        st.update(state='starting', message='正在启动浏览器并准备二维码…')
    return {'mode': 'qr', **st}


@app.get("/api/login/{platform}/status")
async def api_login_status(platform: str, persona: str = ''):
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    s = _login_status(platform, persona)
    if s.get('state') == 'success':
        # 登录刚成功 → 清掉登录前缓存的「未登录」whoami 结果，令下次 whoami 重新真校验；
        # 否则卡片会因 WHOAMI_TTL(600s) 内的旧 false 持续显示「未登录」（本次视频号问题的根因）。
        # 只清缓存、不改任何登录/检测逻辑。
        with _WHOAMI_LOCK:
            _WHOAMI_CACHE.pop(_pk(persona, platform), None)
    return {'mode': 'qr', **s}


class SmsCodeRequest(BaseModel):
    code: str


@app.post("/api/login/{platform}/sms")
async def api_login_sms(platform: str, req: SmsCodeRequest, persona: str = ''):
    """回填短信验证码：写入 runner 轮询的一次性验证码文件（见 login_state.read_sms_code）。按画像隔离。

    登录 runner 检测到风控短信墙时把状态置 sms_required，前端弹输入框，用户把手机
    收到的验证码提交到这里，runner 读走后填码提交，继续完成登录。
    """
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    code = ''.join(ch for ch in (req.code or '') if ch.isdigit())
    if not (4 <= len(code) <= 8):
        raise HTTPException(400, '验证码应为 4-8 位数字')
    d = _login_dir(persona)
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{platform}.code').write_text(code, encoding='utf-8')
    return {'ok': True}


class WechatCredentials(BaseModel):
    appId: str = ''
    appSecret: str = ''
    name: str = ''
    author: str = ''


@app.get("/api/accounts/{platform}/credentials")
async def api_get_credentials(platform: str):
    """读取凭证式平台（目前仅公众号）的已配置状态（AppID 脱敏，AppSecret 不回传）。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get('backend') != 'wechat-oa':
        raise HTTPException(404, '该平台不使用凭证登录')
    acc = _wechat_web_account()
    app_id = acc.get('app_id', '') or ''
    return {
        'configured': bool(app_id and acc.get('app_secret')),
        'appIdMasked': (app_id[:6] + '***' + app_id[-4:]) if len(app_id) > 10 else ('***' if app_id else ''),
        'name': acc.get('name', '') or '',
        'author': acc.get('author', '') or '',
    }


@app.post("/api/accounts/{platform}/credentials")
async def api_save_credentials(platform: str, req: WechatCredentials):
    """保存凭证式平台（公众号）的 AppID/AppSecret，写入 skill 配置并调官方接口验证。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get('backend') != 'wechat-oa':
        raise HTTPException(404, '该平台不使用凭证登录')
    app_id = (req.appId or '').strip()
    app_secret = (req.appSecret or '').strip()
    if not app_id or not app_secret:
        raise HTTPException(400, 'AppID 和 AppSecret 都不能为空')
    _wechat_save_credentials(app_id, app_secret, name=req.name.strip(), author=req.author.strip())
    # 验证要真连微信接口（最长 30s）：丢线程池，避免卡住整个事件循环
    ok, msg = await asyncio.to_thread(_wechat_verify_token)
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE.pop(platform, None)
    if ok:
        _write_login_marker(platform, 'success', req.name.strip() or '微信公众号')
        return {'ok': True, 'message': '公众号凭证已保存并验证通过'}
    # 校验失败：凭证已存（下次改正后可直接重试），但明确告知失败原因（常见 40164 IP 白名单 / 40125 密钥错误）
    return {'ok': False, 'message': f'凭证已保存但验证未通过：{msg}。若是 40164 请把服务器出口 IP 加入公众号 IP 白名单。'}


def _mp_login_status(persona: str = '') -> dict:
    """读公众号后台(mp)登录状态 + 二维码（文件由 weixin_mp_stats.py login 写）。按画像隔离。"""
    d = _login_dir(persona)
    st = d / "wechat-oa-mp.json"
    data = {"state": "unknown", "message": ""}
    if st.is_file():
        try:
            dd = json.loads(st.read_text(encoding="utf-8"))
            data = {"state": dd.get("state", "unknown"), "message": dd.get("message", "")}
        except Exception:
            pass
    proc = LOGIN_PROCESSES.get(_pk(persona, "wechat-oa-mp"))
    if data["state"] not in ("success", "expired", "error") and proc is not None and proc.poll() is not None:
        data = {"state": "error", "message": f"登录程序退出（码 {proc.poll()}），见 {_login_dir_rel(persona)}/wechat-oa-mp.log"}
    qr = d / "wechat-oa-mp.png"
    if data["state"] in ("qr_ready", "window_login") and qr.is_file():
        data["qr"] = f"{_login_dir_rel(persona)}/wechat-oa-mp.png"
        try:
            data["qrTs"] = qr.stat().st_mtime_ns // 1_000_000
        except OSError:
            data["qrTs"] = 0
    else:
        data["qr"] = ""
        data["qrTs"] = 0
    return data


def _stop_mp_login_on_shutdown() -> None:
    """正常重启 Web 时回收所有画像的扫码进程，避免它继续写入下一次登录的状态。"""
    for key in list(LOGIN_PROCESSES):
        if key.endswith("::wechat-oa-mp"):
            proc = LOGIN_PROCESSES.pop(key, None)
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                persona = key.split("::", 1)[0]
                _write_login_marker("wechat-oa-mp", "expired", "服务已重启，请重新扫码登录",
                                    '' if persona == '_general' else persona)


@app.post("/api/accounts/{platform}/mp-login")
async def api_mp_login_start(platform: str, persona: str = ''):
    """启动「公众号后台」扫码登录（数据中心取数用，管理员级会话，独立于 AppID 凭证）。按画像隔离。
    默认直连起 Playwright 出二维码；受限网络可设 EASEL_PROXY / https_proxy 走正向代理。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get("backend") != "wechat-oa":
        raise HTTPException(404, "该平台不使用公众号后台登录")
    key = _pk(persona, "wechat-oa-mp")
    d = _login_dir(persona)
    # 重复点击复用正在进行的登录，不能删除其二维码或启动第二个 Chromium。
    proc = LOGIN_PROCESSES.get(key)
    if proc is not None and proc.poll() is None:
        return {"mode": "qr", **_mp_login_status(persona)}
    d.mkdir(parents=True, exist_ok=True)
    for f in (d / "wechat-oa-mp.png", d / "wechat-oa-mp.json"):
        try:
            f.unlink()
        except OSError:
            pass
    wx_proxy = os.environ.get("EASEL_PROXY") or os.environ.get("https_proxy") or ""
    cmd = [sys.executable, str(SHARED_SCRIPTS / "weixin_mp_stats.py"), "login",
           "--profile-base", str(_persona_profile_base(persona)),
           "--proxy", wx_proxy, "--qr-out", str(d / "wechat-oa-mp.png"),
           "--status-file", str(d / "wechat-oa-mp.json"), "--timeout", "240"]
    log_file = (d / "wechat-oa-mp.log").open("a", encoding="utf-8")
    env = _proxy_env()
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env,
                            stdout=log_file, stderr=subprocess.STDOUT)
    log_file.close()
    LOGIN_PROCESSES[key] = proc
    status = _mp_login_status(persona)
    if status["state"] == "unknown":
        status.update(state="starting", message="正在启动浏览器并准备二维码…")
    return {"mode": "qr", **status}


@app.get("/api/accounts/{platform}/mp-login/status")
async def api_mp_login_status(platform: str, persona: str = ''):
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg or cfg.get("backend") != "wechat-oa":
        raise HTTPException(404, "该平台不使用公众号后台登录")
    return {"mode": "qr", **_mp_login_status(persona)}


@app.get("/api/accounts/{platform}/whoami")
async def api_account_whoami(platform: str, persona: str = '', force: bool = False):
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    key = _pk(persona, platform)
    task = _WHOAMI_TASKS.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_check_account_whoami(platform, persona, force))
        _WHOAMI_TASKS[key] = task
        def finish(completed):
            if _WHOAMI_TASKS.get(key) is completed:
                _WHOAMI_TASKS.pop(key, None)

        task.add_done_callback(finish)
    return await asyncio.shield(task)


async def _check_account_whoami(platform: str, persona: str = '', force: bool = False):
    """真校验登录态 + 读昵称/头像（起 headless 浏览器，数秒）。按画像隔离登录态 + cookie/profile。
    带 TTL 进程内缓存（避免账号页+工作台重复起浏览器）；确认已登录则回写标记，令快速路径自愈。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    backend = cfg['backend']
    if backend == 'unsupported':
        return {'loggedIn': False, 'name': '', 'avatar': ''}
    key = _pk(persona, platform)
    pk_mp = _pk(persona, 'wechat-oa-mp')
    mkfile = 'wechat-oa-mp' if backend == 'wechat-oa' else platform
    if backend == 'wechat-oa':
        if _mp_login_status(persona)['state'] != 'success':
            acc = _wechat_web_account()
            return {'loggedIn': _account_logged_in(platform, cfg, persona),
                    'name': acc.get('name', '') or '微信公众号', 'avatar': ''}
    proc_key = pk_mp if backend == 'wechat-oa' else key
    login_process = LOGIN_PROCESSES.get(proc_key)
    if login_process is not None and login_process.poll() is None:
        if (_mp_login_status(persona) if backend == 'wechat-oa' else _login_status(platform, persona))['state'] == 'success':
            try:
                await asyncio.to_thread(login_process.wait, timeout=8)
                if login_process.poll() is None:
                    raise HTTPException(409, '登录已确认，浏览器仍在保存账号，请稍后重试校验')
            except subprocess.TimeoutExpired:
                raise HTTPException(409, '登录已确认，浏览器仍在保存账号，请稍后重试校验')
        else:
            with _WHOAMI_LOCK:
                previous = _WHOAMI_CACHE.get(key)
            return previous[1] if previous else {
                'loggedIn': _account_logged_in(platform, cfg, persona), 'name': '', 'avatar': '',
            }
    d = _login_dir(persona)
    marker_path = d / f'{mkfile}.json'
    def marker_version():
        try:
            return marker_path.read_bytes()
        except OSError:
            return None

    initial_marker = marker_version()
    with _WHOAMI_LOCK:
        hit = _WHOAMI_CACHE.get(key)
    cache_ttl = (min(WHOAMI_TTL, 60) if hit and hit[1].get('loggedIn')
                 and (not hit[1].get('name') or not hit[1].get('avatar')) else WHOAMI_TTL)
    if (not force and hit and (time.time() - hit[0]) < cache_ttl
            and hit[1]['loggedIn'] == _account_logged_in(platform, cfg, persona)):
        return hit[1]
    profile_base = str(_persona_profile_base(persona))
    if backend == 'biliup':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'bili_login.py'), 'whoami',
               '--cookie', str(_persona_cookie_file(persona))]
    elif backend == 'xhs':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'xhs_publish.py'), 'whoami', '--no-proxy',
               '--profile-base', profile_base]
    elif backend == 'wechat-oa':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'weixin_mp_stats.py'), 'whoami', '--no-proxy',
               '--profile-base', profile_base]
    elif backend == 'douyin':
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'douyin_publish.py'), 'whoami', '--no-proxy',
               '--profile-base', profile_base]
    else:
        cmd = [sys.executable, str(SHARED_SCRIPTS / 'web_publisher.py'), 'whoami',
               '--platform', cfg['wp'], '--profile-base', profile_base]
    try:
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=str(PROJECT_ROOT), env=_proxy_env(),
                                       capture_output=True, text=True, encoding='utf-8',
                                       timeout=35 if backend == 'xhs' else 40 if backend in ('douyin', 'wechat-oa') else 60)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, '校验超时（浏览器起不来或网络慢）')
    data = {'loggedIn': False, 'name': '', 'avatar': ''}
    confident = False
    for line in reversed((proc.stdout or '').strip().splitlines()):
        line = line.strip()
        if line.startswith('{'):
            try:
                dd = json.loads(line)
                data = {'loggedIn': bool(dd.get('loggedIn')), 'name': dd.get('name') or '', 'avatar': dd.get('avatar') or ''}
                # 有 error 字段 = 校验本身失败（浏览器起不来/网络抖动/崩溃），不是可信的「未登录」结论
                confident = proc.returncode == 0 and isinstance(dd.get('loggedIn'), bool) and not dd.get('error')
                break
            except Exception:
                continue
    current_process = LOGIN_PROCESSES.get(proc_key)
    if (marker_version() != initial_marker
            or (current_process is not None and current_process.poll() is None)):
        return {'loggedIn': _account_logged_in(platform, cfg, persona), 'name': '', 'avatar': ''}
    if not confident:
        raise HTTPException(502, '未能确认当前账号身份，请稍后重试校验；原登录状态和账号资料保持不变')
    verified_logged_in = data['loggedIn']
    if backend == 'wechat-oa' and not verified_logged_in and _wechat_has_credentials():
        account = _wechat_web_account()
        data = {'loggedIn': True, 'name': account.get('name', '') or '微信公众号', 'avatar': ''}
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE[key] = (time.time(), data)
    # 回写标记：确认已登录 → 快速路径（/api/accounts、/api/analytics/platforms）此后也正确；
    # biliup 走 cookie 文件判定，不用标记文件。
    if backend != 'biliup':
        if verified_logged_in:
            _write_login_marker(mkfile, 'success', data.get('name') or '', persona)
        else:
            try:
                marker_path.unlink()
            except OSError:
                pass
    return data


@app.post("/api/logout/{platform}")
async def api_logout(platform: str, persona: str = ''):
    """退出登录：删持久化浏览器 profile + 登录状态/二维码/头像文件（biliup 删 cookie 文件）。按画像隔离。"""
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    deleted = []
    d = _login_dir(persona)
    if cfg['backend'] == 'wechat-oa':
        # 1) 清 AppID 凭证（旧配置兜底）
        _wechat_clear_credentials()
        deleted.append('wechat-publisher.yaml:accounts.web')
        # 2) 停掉可能仍在跑的后台登录进程（避免它又写回 success 标记）
        proc = LOGIN_PROCESSES.pop(_pk(persona, 'wechat-oa-mp'), None)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        # 3) 删 AppID 标记 + mp 后台会话标记/二维码/日志（登录态判定看的就是 wechat-oa-mp.json）
        for name in (f'{platform}.json', f'{platform}.png',
                     'wechat-oa-mp.json', 'wechat-oa-mp.png', 'wechat-oa-mp.log'):
            f = d / name
            try:
                if f.is_file():
                    f.unlink()
                    deleted.append(f.name)
            except OSError:
                pass
        # 4) 删 mp 后台浏览器持久化会话（真正退出登录，按画像隔离）
        mpdir = (_persona_profile_base(persona) / 'WeixinMpProfile').resolve()
        if _persona_profile_base(persona).resolve() in mpdir.parents and mpdir.is_dir():
            shutil.rmtree(mpdir, ignore_errors=True)
            deleted.append('WeixinMpProfile')
        with _WHOAMI_LOCK:
            _WHOAMI_CACHE.pop(_pk(persona, platform), None)
        return {'ok': True, 'deleted': deleted}
    proc = LOGIN_PROCESSES.pop(_pk(persona, platform), None)
    if proc is not None and proc.poll() is None:
        # 与上面公众号分支同理：停掉仍在跑的登录进程，否则它会把 success 标记
        # 又写回 {platform}.json，账号在退出后“复活”成已登录。
        try:
            proc.terminate()
            await asyncio.to_thread(proc.wait, timeout=5)
        except Exception:
            pass
    prof_name = cfg.get('profile')
    if prof_name:
        base = _persona_profile_base(persona).resolve()
        pdir = (base / prof_name).resolve()
        if base in pdir.parents and pdir.is_dir():
            shutil.rmtree(pdir, ignore_errors=True)
            deleted.append(prof_name)
    if cfg['backend'] == 'biliup':
        ck = _persona_cookie_file(persona)
        if ck.is_file():
            ck.unlink()
            deleted.append('cookies.json')
    for suffix in ('.json', '.png', '-me.png', '.code'):
        f = d / f'{platform}{suffix}'
        try:
            if f.is_file():
                f.unlink()
                deleted.append(f.name)
        except OSError:
            pass
    with _WHOAMI_LOCK:
        _WHOAMI_CACHE.pop(_pk(persona, platform), None)
    return {'ok': True, 'deleted': deleted}


# 归因层：可抓创作数据的平台（多数走 Playwright 登录态；bilibili 用 biliup cookies 不起浏览器、
# wechat-oa 走 mp 后台会话 Playwright 拦截数据 XHR）
ANALYTICS_PLATFORMS = {"xiaohongshu", "douyin", "kuaishou", "zhihu", "weixin-channels", "bilibili", "wechat-oa"}
_analytics_inflight: dict[str, asyncio.Task] = {}


def _run_analytics_command(cmd: list[str], env: dict[str, str], timeout: int):
    process = subprocess.Popen(
        cmd, cwd=str(PROJECT_ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=os.name != "nt")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.stdout.close()
            process.stderr.close()
            process.wait(timeout=5)
        raise


@app.get("/api/analytics/platforms")
async def api_analytics_platforms(persona: str = ''):
    """列出支持抓数据的平台 + 各自登录态（前端据此渲染平台选择器）。按画像隔离。"""
    return [
        {"platform": pf, "name": LOGIN_RUNNERS.get(pf, {}).get("name", pf),
         "loggedIn": _account_logged_in(pf, LOGIN_RUNNERS.get(pf, {}), persona)}
        for pf in LOGIN_RUNNERS if pf in ANALYTICS_PLATFORMS
    ]


@app.get("/api/analytics/{platform}")
async def api_analytics(platform: str, persona: str = ''):
    """抓取某平台（当前画像）已登录账号的创作数据，同(画像,平台)并发刷新共享一次有时限的任务。"""
    if platform not in ANALYTICS_PLATFORMS:
        raise HTTPException(404, "该平台暂不支持数据抓取")
    key = _pk(persona, platform)
    task = _analytics_inflight.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_fetch_analytics(platform, persona))
        _analytics_inflight[key] = task
        task.add_done_callback(lambda finished: _finish_analytics(key, finished))
    return await asyncio.shield(task)


def _finish_analytics(key: str, task: asyncio.Task) -> None:
    if _analytics_inflight.get(key) is task:
        _analytics_inflight.pop(key, None)
    if not task.cancelled():
        task.exception()


async def _fetch_analytics(platform: str, persona: str = ''):
    # B站用 cookie 调 API（无浏览器 profile）、公众号走 mp 后台会话（Playwright 拦截数据 XHR，见下），单独分支；其余走 account_stats（Playwright）。按画像隔离 cookie/profile。
    profile_base = str(_persona_profile_base(persona))
    if platform == "bilibili":
        cmd = [sys.executable, str(SHARED_SCRIPTS / "bili_login.py"), "stats",
               "--cookie", str(_persona_cookie_file(persona))]
    elif platform == "wechat-oa":
        # 公众号数据走「后台网页端」(mp.weixin.qq.com 管理员会话 + Playwright 拦截数据 XHR)：
        # 开发者 datacube 接口需认证+群发+接口权限，多数号取不到；后台端有登录态即可看到发表记录/数据。
        # 需先扫码登录 mp 后台（weixin_mp_stats.py login）；默认直连，受限网络才需 EASEL_PROXY/https_proxy。
        wx_proxy = os.environ.get("EASEL_PROXY") or os.environ.get("https_proxy") or ""
        cmd = [sys.executable, str(SHARED_SCRIPTS / "weixin_mp_stats.py"), "stats",
               "--profile-base", profile_base, "--proxy", wx_proxy, "--count", "20"]
    else:
        # 代理策略由 account_stats.py 按平台自定（xhs 直连、其它走 env），后端照常传 _proxy_env
        cmd = [sys.executable, str(SHARED_SCRIPTS / "account_stats.py"), "fetch",
               "--platform", platform, "--profile-base", profile_base]
    ana_env = _proxy_env()
    ana_env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        proc = await asyncio.to_thread(
            _run_analytics_command, cmd, ana_env, 180 if platform == "wechat-oa" else 90)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "抓取超时，已停止浏览器；请检查网络，关闭正在进行的登录或校验后重试")
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                result = json.loads(line)
            except Exception:
                continue
            if not isinstance(result, dict):
                continue
            if proc.returncode or result.get("error"):
                raise HTTPException(502, str(result.get("error") or "平台数据抓取失败")[:240])
            if (result.get("loggedIn") is True
                    and all(result.get(key) is None for key in ("followers", "likes", "following", "posts"))
                    and not result.get("metrics") and not result.get("notes")):
                raise HTTPException(502, "账号已登录，但平台未返回可用数据；请检查网络或平台维护状态后重试")
            return result
    detail = (proc.stderr or "").strip().splitlines()[-1:] or ["未取到数据"]
    raise HTTPException(502, f"未取到数据（可能未登录或平台改版）：{detail[0][:120]}")


MEDIA_REQUIRED = {"xiaohongshu", "douyin", "kuaishou", "weixin-channels", "bilibili"}
VIDEO_ONLY_PUBLISH = {"douyin", "weixin-channels", "bilibili"}   # 只能发视频的平台


class PublishRequest(BaseModel):
    title: str = ''
    body: str = ''
    media: list[str] = []
    tags: str = ''
    persona: str = ''


class ManualPublishRequest(BaseModel):
    title: str = Field(default='', max_length=1000)
    body: str = Field(default='', max_length=100000)
    tags: str = Field(default='', max_length=2000)
    media: list[str] = Field(default_factory=list, max_length=32)
    reviewed: StrictBool = False


@app.get("/api/publishing-policy")
def api_publishing_policy():
    mode = publishing_mode(read_settings(ENV_FILE))
    return {"mode": mode, "automaticSubmission": mode == "automated"}


def _require_automated_publishing() -> None:
    if publishing_mode(read_settings(ENV_FILE)) != "automated":
        raise HTTPException(403, '当前为人工发布模式，请审核后复制或导出，在平台官方发布页亲自提交')


@app.post("/api/manual-publish/{platform}/export")
def api_manual_publish_export(platform: str, req: ManualPublishRequest):
    """Build a local handoff package; never contact a platform or mark it published."""
    try:
        package = build_package(OUTPUTS_DIR, platform, req.title, req.body,
                                req.tags, req.media, req.reviewed)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"path": package.relative_to(OUTPUTS_DIR.resolve()).as_posix(),
            "filename": package.name, "published": False}


def _write_publish_status(status_file: Path, state: str, message: str = '') -> None:
    """写异步发布状态（与 login_state 同格式），原子写。"""
    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = status_file.with_suffix('.tmp')
        tmp.write_text(json.dumps({'state': state, 'message': message, 'ts': int(time.time())},
                                  ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, status_file)
    except Exception:
        pass


def _publish_status_file(platform: str, persona: str = '') -> Path:
    """异步发布状态文件：命名画像按画像隔离，通用/空画像沿用全局（向后兼容）。"""
    d = PUBLISH_DIR / persona if _valid_login_persona(persona) else PUBLISH_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f'{platform}.json'


def _publish_code_file(platform: str, persona: str = '') -> Path:
    """异步发布短信验证码回填文件：按画像隔离。"""
    d = PUBLISH_DIR / persona if _valid_login_persona(persona) else PUBLISH_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f'{platform}.code'


def _read_publish_status(platform: str, persona: str = '') -> dict:
    st = _publish_status_file(platform, persona)
    if st.is_file():
        try:
            d = json.loads(st.read_text(encoding='utf-8'))
            return {'state': d.get('state', 'unknown'), 'message': d.get('message', '')}
        except Exception:
            pass
    return {'state': 'unknown', 'message': ''}


def _record_successful_publish(title: str, body: str, cfg: dict, profile: str = '',
                               tags: str = '', ptype: str = '') -> None:
    try:
        scripts_dir = str(SHARED_SCRIPTS)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import calendar_ops
        with _schedule_lock():
            calendar_ops.record_publish(cfg['name'], title, note=body[:200],
                                        tags=tags, ptype=ptype,
                                        source='publish-page', profile=profile,
                                        data_path=SCHEDULE_FILE, forward_log=True)
    except Exception:
        pass


def _run_publish_bg(platform: str, cmd: list, title: str, body: str, cfg: dict,
                    status_file: Path, code_file: Path, profile: str = '',
                    tags: str = '', ptype: str = '') -> None:
    """后台线程跑发布脚本（脚本自身把 starting/sms_required/verifying/success/error 写进 status_file）。
    结束后兜底补写终态 + 记 _publish.log + 成功则回流排期。"""
    ok = False
    out = err = ''
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=_publish_env(),
                              capture_output=True, text=True, timeout=900)
        ok = proc.returncode == 0
        out, err = proc.stdout or '', proc.stderr or ''
    except subprocess.TimeoutExpired:
        err = '发布超时（>900s）'
    except Exception as e:  # noqa: BLE001
        err = f'发布进程异常：{e}'
    try:
        with (OUTPUTS_DIR / '_publish.log').open('a', encoding='utf-8') as lf:
            lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {platform}(async) ok={ok} =====\n")
            lf.write('CMD: ' + ' '.join(cmd) + '\nSTDOUT:\n' + out[-2000:] + '\nSTDERR:\n' + err[-2000:] + '\n')
    except Exception:
        pass
    # 脚本正常会写终态；异常/超时没写到时兜底补一个
    if _read_publish_status(platform, profile)['state'] not in ('success', 'error'):
        _write_publish_status(status_file, 'success' if ok else 'error',
                              '发布成功' if ok else ('\n'.join((err or out).strip().splitlines()[-4:]) or '发布失败'))
    try:
        code_file.unlink()
    except OSError:
        pass
    if ok:
        _record_successful_publish(title, body, cfg, profile, tags, ptype)


def _start_async_publish(platform: str, cmd: list, title: str, body: str, cfg: dict,
                         status_file: Path, code_file: Path, profile: str = '',
                         tags: str = '', ptype: str = '') -> dict:
    """启动异步发布：清旧码/状态 → 起后台线程 → 立即返回。前端轮询 /api/publish/{p}/status，
    遇 sms_required 弹输入框、提交到 /api/publish/{p}/sms。"""
    try:
        code_file.unlink()
    except OSError:
        pass
    _write_publish_status(status_file, 'starting', '发布中…（若触发风控会要求短信验证）')
    threading.Thread(target=_run_publish_bg,
                     args=(platform, cmd, title, body, cfg, status_file, code_file, profile, tags, ptype),
                     daemon=True).start()
    # 关键：**不返回 ok:true**——这只是「已启动」的应答，真正结果要靠轮询 /status。
    # 若这里给 ok:true，旧前端会把它当「已发布」立刻显示成功（假成功 bug，真机踩过）。
    return {'async': True, 'pending': True, 'message': '发布已启动，请稍候…'}


@app.get("/api/publish/{platform}/status")
async def api_publish_status(platform: str, persona: str = ''):
    """轮询异步发布状态：starting/sms_required/verifying/success/error。"""
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    return {'mode': 'publish', **_read_publish_status(platform, persona)}


@app.post("/api/publish/{platform}/sms")
async def api_publish_sms(platform: str, req: SmsCodeRequest, persona: str = ''):
    """发布触发短信墙时回填验证码（写发布 runner 轮询的一次性验证码文件）。"""
    _require_automated_publishing()
    if platform not in LOGIN_RUNNERS:
        raise HTTPException(404, '未知平台')
    code = ''.join(ch for ch in (req.code or '') if ch.isdigit())
    if not (4 <= len(code) <= 8):
        raise HTTPException(400, '验证码应为 4-8 位数字')
    _publish_code_file(platform, persona).write_text(code, encoding='utf-8')
    return {'ok': True}


@app.post("/api/publish/{platform}")
async def api_publish(platform: str, req: PublishRequest):
    """一键发布：分发到对应 publisher 脚本真发（--exec）。二次确认在前端。"""
    _require_automated_publishing()
    cfg = LOGIN_RUNNERS.get(platform)
    if not cfg:
        raise HTTPException(404, '未知平台')
    backend = cfg['backend']
    if backend == 'unsupported':
        raise HTTPException(400, f"{cfg['name']} 暂不支持一键发布")
    profile = (req.persona or '').strip()
    if profile and (not valid_persona_name(profile) or not profile_exists(profile)):
        raise HTTPException(400, '选择的画像不存在或名称非法')
    pbase = str(_persona_profile_base(profile))
    if not req.title.strip() and not req.body.strip():
        raise HTTPException(400, '标题/正文不能为空')
    imgs, vids = [], []
    for rel in req.media or []:
        full = _safe_output_path(rel)
        ext = full.suffix.lower()
        if ext in VIDEO_EXTS:
            vids.append(str(full))
        elif ext in IMAGE_EXTS:
            imgs.append(str(full))
    if platform in MEDIA_REQUIRED and not imgs and not vids:
        raise HTTPException(400, f"{cfg['name']} 需附带图片或视频")
    if imgs and vids:
        raise HTTPException(400, '同一条内容不能同时发图片和视频，请二选一')
    if platform in VIDEO_ONLY_PUBLISH and not vids:
        raise HTTPException(400, f"{cfg['name']} 只能发视频，请附带一个视频文件")
    title = req.title.strip() or req.body.strip()[:20]
    tags = req.tags or ''
    publish_type = '视频' if vids else ('文章' if platform == 'wechat-oa' else '图文')
    py = sys.executable
    if platform == 'xiaohongshu':
        base = [py, str(SHARED_SCRIPTS / 'xhs_publish.py')]
        cmd = base + ['publish-video', '--no-proxy', '--video', vids[0]] if vids else base + ['publish', '--no-proxy', '--images', ','.join(imgs)]
        cmd += ['--title', title, '--content', req.body, '--tags', tags, '--profile-base', pbase, '--exec']
    elif platform == 'bilibili':
        # B站投稿：直接调 biliup CLI（需 cookies.json，PATH 上有 biliup）。必须视频；
        # tid=36「知识」；B站投稿必须≥1 标签，无则兜底「日常」。
        bili_tag = tags.replace('#', '').replace('，', ',').strip().strip(',') or '日常'
        cmd = ['biliup', '-u', str(_persona_cookie_file(profile)), 'upload', vids[0],
               '--title', title[:80], '--tid', '36', '--copyright', '1', '--tag', bili_tag]
        if req.body.strip():
            cmd += ['--desc', req.body[:2000]]
    elif platform == 'douyin':
        base = [py, str(SHARED_SCRIPTS / 'douyin_publish.py')]
        cmd = base + ['publish-video', '--no-proxy', '--video', vids[0]] if vids else base + ['publish', '--no-proxy', '--images', ','.join(imgs)]
        cmd += ['--title', title, '--content', req.body, '--tags', tags, '--profile-base', pbase, '--exec']
        # 抖音发布可能触发风控短信墙——异步跑 + 状态/验证码文件，前端轮询到 sms_required 时弹输入框
        status_file = _publish_status_file('douyin', profile)
        code_file = _publish_code_file('douyin', profile)
        cmd += ['--status-file', str(status_file), '--sms-code-file', str(code_file)]
        return _start_async_publish(platform, cmd, title, req.body, cfg, status_file, code_file,
                                    profile, tags, publish_type)
    elif platform == 'wechat-oa':
        # 微信公众号：走「后台会话」发布（免 AppID/AppSecret、免 IP 白名单）。
        # 正文 MD → 公众号 HTML（skill 排版器）→ 会话建草稿（weixin_mp_stats.py publish，内嵌图传 mp CDN）。
        if _mp_login_status(profile).get('state') != 'success':
            raise HTTPException(400, '公众号后台未登录：请先在账号页点「登录公众号后台」扫码')
        if not imgs:
            raise HTTPException(400, '公众号文章需要一张封面图，请附带至少一张图片')
        if vids:
            raise HTTPException(400, '公众号发图文文章，请附带封面/正文图片而非视频')
        cover = imgs[0]
        PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = uuid.uuid4().hex[:12]
        md_path = PUBLISH_DIR / f'wechat-oa-{stamp}.md'
        html_path = PUBLISH_DIR / f'wechat-oa-{stamp}.html'
        body_md = req.body or ''
        extra_imgs = imgs[1:]
        if extra_imgs:
            body_md += "\n\n" + "\n\n".join(f'![]({p})' for p in extra_imgs)
        md_path.write_text(f"# {title}\n\n{body_md}\n", encoding='utf-8')
        conv = await asyncio.to_thread(
            subprocess.run, [py, str(WECHAT_SKILL_SCRIPTS / 'html_converter.py'),
                             str(md_path), '-o', str(html_path)],
            cwd=str(PROJECT_ROOT), env=_proxy_env(),
            capture_output=True, text=True, timeout=60)
        if conv.returncode != 0 or not html_path.is_file():
            raise HTTPException(500, f"排版失败：{(conv.stderr or conv.stdout or '')[-200:]}")
        wx_proxy = os.environ.get('EASEL_PROXY') or os.environ.get('https_proxy') or ''
        cmd = [py, str(SHARED_SCRIPTS / 'weixin_mp_stats.py'), 'publish', '--proxy', wx_proxy,
               '--profile-base', pbase,
               '--html', str(html_path), '--cover', cover, '--title', title,
               '--digest', (req.body or '').strip()[:100], '--author', '']
    else:
        cmd = [py, str(SHARED_SCRIPTS / 'web_publisher.py'), 'publish',
               '--profile-base', pbase,
               '--platform', cfg['wp'], '--title', title, '--desc', req.body,
               '--tags', tags, '--exec']
        media = vids[0] if vids else (imgs[0] if imgs else None)
        if media:
            cmd += ['--media', media]
    # 公众号走后台会话（Playwright，脚本自带 --proxy，默认直连）；其余平台走 _publish_env
    pub_env = _proxy_env() if platform == 'wechat-oa' else _publish_env()
    try:
        proc = await asyncio.to_thread(subprocess.run, cmd, cwd=str(PROJECT_ROOT), env=pub_env,
                                       capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, '发布超时（媒体处理慢或流程卡住）')
    ok = proc.returncode == 0
    tail = (proc.stderr or proc.stdout or '').strip().splitlines()
    detail = '\n'.join(tail[-8:])
    try:
        with (OUTPUTS_DIR / '_publish.log').open('a', encoding='utf-8') as lf:
            lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {platform} rc={proc.returncode} ok={ok} =====\n")
            lf.write('CMD: ' + ' '.join(cmd) + '\n')
            lf.write('STDOUT:\n' + (proc.stdout or '')[-2000:] + '\n')
            lf.write('STDERR:\n' + (proc.stderr or '')[-2000:] + '\n')
    except Exception:
        pass
    if ok:
        _record_successful_publish(title, req.body, cfg, profile, tags, publish_type)
    return {'ok': ok, 'message': '发布成功' if ok else '发布失败（见 detail）', 'detail': detail}


class ProfileBuildRequest(BaseModel):
    name: str
    form: dict


@app.post("/api/profile/build")
async def api_profile_build(req: ProfileBuildRequest):
    """首次引导：表单 → 写基线画像（确定性，秒可用）→ **后台**跑 agent 分析社媒链接增强。

    改异步：立即返回（基线已写、画像即可用），避免 agent 增强(~2min)阻塞请求被 code-server
    代理超时掐断（前端曾因此报 API 400）。前端轮询 /api/profile/build/status/{name} 看增强进度。
    """
    name = (req.name or '').strip()  # 自动去掉首尾空格
    if not name:
        raise HTTPException(400, '画像名不能为空（去掉首尾空格后为空，请输入有效名称）')
    if '/' in name or '\\' in name:
        raise HTTPException(400, '画像名不能包含 / 或 \\ 字符，请改掉后重试')
    if name.startswith(('.', '_')):
        raise HTTPException(400, '画像名不能以 . 或 _ 开头，请换个开头')
    pd = PROFILES_DIR / name
    if pd.exists():
        raise HTTPException(409, f'画像「{name}」已存在，请换一个名字')
    _write_baseline_profile(name, req.form or {})
    instruction = _form_to_instruction(name, req.form or {})
    msg = (f"请执行 /skill-profile-builder 完善已存在的画像「{name}」。用户已通过表单提供以下信息，我已按此写好 profiles/{name}"
           f"/ 的基线六维文件。请：①尽力抓取用户给的社媒链接分析已发内容/风格/受众（抓不到就降级，标注[待补充]，勿臆造）②据分析结果润色/补全各维度文件 ③给出一句话完成度摘要。表单信息如下：\n\n{instruction}")

    _write_profile_status(name, 'running', 'AI 正在分析并增强画像…')

    def _enhance() -> None:
        try:
            log = run_agent_sync(msg, TIMEOUT_PRODUCE)
            _write_profile_status(name, 'done', log)
        except Exception as e:  # noqa: BLE001
            _write_profile_status(name, 'failed', f'AI 增强失败（基线画像已可用）：{e}')

    threading.Thread(target=_enhance, daemon=True).start()
    # 基线已写、画像立即可用；增强在后台，前端轮询状态
    return {'created': pd.is_dir(), 'name': name, 'async': True, 'status': 'running'}


def _profile_status_file(name: str) -> Path:
    return PROFILE_BUILD_DIR / f'{name}.json'


def _write_profile_status(name: str, state: str, log: str = '') -> None:
    """原子写画像增强状态。"""
    try:
        PROFILE_BUILD_DIR.mkdir(parents=True, exist_ok=True)
        f = _profile_status_file(name)
        tmp = f.with_suffix('.tmp')
        tmp.write_text(json.dumps({'state': state, 'log': log, 'ts': int(time.time())},
                                  ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, f)
    except Exception:
        pass


@app.get("/api/profile/build/status/{name}")
async def api_profile_build_status(name: str):
    """查画像增强进度：running / done / failed / unknown。"""
    f = _profile_status_file(name)
    if f.is_file():
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            return {'state': d.get('state', 'unknown'), 'log': d.get('log', '')}
        except Exception:
            pass
    return {'state': 'unknown', 'log': ''}


def _form_to_instruction(name: str, form: dict) -> str:
    def g(k: str, default: str = '（未填）') -> str:
        v = form.get(k)
        if isinstance(v, list):
            return '、'.join(str(x) for x in v) if v else default
        return str(v).strip() if v not in (None, '') else default
    links = form.get('links') or {}
    links_txt = '\n'.join(f'  - {p}: {u}' for p, u in links.items() if u) or '  （未提供）'
    return (f"画像名：{name}\n运营平台：{g('platforms')}\n起号状态：{g('accountStage')}"
            f"\n社媒主页链接：\n{links_txt}\n想做的方向：{g('direction')}"
            f"\n为什么做/我的优势：{g('reason')}\n运营目标：{g('goal')}"
            f"\n想产出的形式：{g('formats')}\n喜欢看的内容/对标账号：{g('likes')}"
            f"\n期望调性：{g('tone')}\n不做的内容/红线：{g('avoid')}\n")


def _write_baseline_profile(name: str, form: dict) -> None:
    """从表单确定性生成六维基线文件。链接派生字段标 [待 AI 分析]。"""
    pd = PROFILES_DIR / name
    pd.mkdir(parents=True, exist_ok=True)

    def g(k: str, default: str = '') -> str:
        v = form.get(k)
        if isinstance(v, list):
            return '、'.join(str(x) for x in v)
        return str(v).strip() if v not in (None, '') else default
    direction = g('direction') or '[待补充]'
    reason = g('reason') or '[待补充]'
    goal = g('goal')
    formats = g('formats')
    tone = g('tone') or '[待分析]'
    likes = g('likes')
    avoid = g('avoid')
    platforms = form.get('platforms') or []
    links = form.get('links') or {}
    (pd / 'identity.md').write_text(
        f"# 身份定位\n\n## 我是谁\n\n{direction}\n\n## 差异化\n\n{reason}\n\n## 内容方向\n\n{direction}"
        f"{'（形式：' + formats + '）' if formats else ''}\n"
        f"{'运营目标：' + goal if goal else ''}\n",
        encoding='utf-8')
    (pd / 'style.md').write_text(
        f"# 内容风格\n\n## 语气\n\n{tone}\n\n## 开头结构\n\n[待 AI 分析已发内容]\n\n## 视觉风格\n\n[待 AI 分析]\n\n## 内容节奏\n\n{formats or '[待补充]'}\n\n## 标志性元素\n\n[待 AI 分析]\n",
        encoding='utf-8')
    (pd / 'audience.md').write_text(
        '# 目标受众\n\n## 核心人群\n\n[待 AI 分析/待补充]\n\n## 兴趣标签\n\n[待补充]\n\n## 痛点\n\n[待补充]\n\n## 互动特征\n\n[待 AI 分析已发内容]\n',
        encoding='utf-8')
    plat_lines = []
    for p in platforms:
        url = links.get(p, '')
        plat_lines.append(f"## {p}\n\n主页：{url or '[待补充]'}\n粉丝量级 / 内容形式：[待补充]\n")
    (pd / 'platforms.md').write_text(
        '# 平台运营\n\n' + ('\n'.join(plat_lines) if plat_lines else '[待补充]\n'),
        encoding='utf-8')
    (pd / 'preferences.md').write_text(
        f"# 偏好与红线\n\n## 要做的\n\n{direction}\n\n## 不做的\n\n{avoid or '[待补充]'}\n\n## 合规底线\n\n{avoid or '[待补充]'}\n",
        encoding='utf-8')
    (pd / 'memory.md').write_text(
        f"# 经验沉淀\n\n## 内容洞察\n\n{'喜欢的内容/对标：' + likes if likes else '[待 AI 分析已收藏/点赞]'}\n\n## 踩过的坑\n\n[待积累]\n",
        encoding='utf-8')


@app.delete("/api/session/{session_key}")
async def api_delete_session(session_key: str):
    """删除本地的 session 记录。"""
    if session_key in _RUNNING_CHAT or _session_lock(session_key).locked():
        raise HTTPException(409, "请先停止当前对话再删除")
    record = harness_store.binding(SESSIONS_DIR, session_key)
    if record:
        from easel.harnesses.rpc import close_sessions
        await close_sessions(session_key)
        harness_store.binding_path(SESSIONS_DIR, session_key).unlink(missing_ok=True)
        _turn_file(f"web:{session_key}").unlink(missing_ok=True)
        return {"deleted": True, "nativeHistoryDeleted": False}
    direct_history = history_path(SESSIONS_DIR, session_key)
    if direct_history.is_file():
        if session_key in _RUNNING_CHAT:
            raise HTTPException(409, "请先停止当前对话再删除")
        direct_history.unlink()
        _turn_file(f"web:{session_key}").unlink(missing_ok=True)
        return {"deleted": True}
    return {"deleted": False, "reason": "session not found"}


TREND_SOURCES: dict[str, tuple[str, str | None]] = {
    "weibo": ("https://v2.xxapi.cn/api/weibohot", "https://60s.viki.moe/v2/weibo"),
    "douyin": ("https://v2.xxapi.cn/api/douyinhot", "https://60s.viki.moe/v2/douyin"),
    "zhihu": ("https://api.zhihu.com/topstory/hot-list?limit=50", "https://60s.viki.moe/v2/zhihu"),
    "bilibili": ("https://v2.xxapi.cn/api/bilibilihot", "https://60s.viki.moe/v2/bili"),
    "baidu": ("https://v2.xxapi.cn/api/baiduhot", "https://60s.viki.moe/v2/baidu/hot"),
    "toutiao": ("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
                "https://60s.viki.moe/v2/toutiao"),
}
TREND_LABELS = {
    "weibo": "微博",
    "douyin": "抖音",
    "zhihu": "知乎",
    "bilibili": "B站",
    "baidu": "百度",
    "toutiao": "头条",
}
_TREND_CACHE: dict[str, tuple[float, list]] = {}
_HOT_TITLE_KEYS = ("title", "Title", "word", "name", "keyword")
_HOT_HOT_KEYS = ("hot", "hot_value", "HotValue", "hotValue", "num", "detail_text")
_HOT_URL_KEYS = ("url", "Url", "link", "mobil_url")


def _http_get_json(url: str, timeout: int = 8):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _pick_str(data: object, keys: tuple[str, ...]) -> str:
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _trend_web_url(url: str) -> str:
    match = re.fullmatch(r'https?://api\.zhihu\.com/questions/(\d+)', url)
    return f'https://www.zhihu.com/question/{match.group(1)}' if match else url


def _parse_hot(obj: object) -> list[dict]:
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict):
        data = data.get("data") or data.get("list") or []
    out: list[dict] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if isinstance(item, str):
            if item.strip():
                out.append({"title": item.strip(), "hot": "", "url": ""})
            continue
        if not isinstance(item, dict):
            continue
        target = item.get("target") if isinstance(item.get("target"), dict) else {}
        title = _pick_str(item, _HOT_TITLE_KEYS) or _pick_str(target, _HOT_TITLE_KEYS)
        if not title:
            continue
        hot = _pick_str(item, _HOT_HOT_KEYS) or _pick_str(target.get("metrics_area"), ("text",))
        url = _pick_str(item, _HOT_URL_KEYS) or _pick_str(target, _HOT_URL_KEYS)
        out.append({"title": title, "hot": hot, "url": _trend_web_url(url)})
    return out


def _fetch_platform(pf: str) -> list[dict]:
    primary, backup = TREND_SOURCES.get(pf, (None, None))
    for url in (primary, backup):
        if not url:
            continue
        try:
            items = _parse_hot(_http_get_json(url))
            if items:
                return items
        except Exception:
            continue
    return []


@app.get("/api/trends")
async def api_trends(platforms: str = "weibo,douyin,zhihu", limit: int = 12):
    pfs = [p.strip() for p in platforms.split(",") if p.strip() in TREND_SOURCES]
    now = time.time()
    loop = asyncio.get_event_loop()
    result = []
    for pf in pfs:
        c = _TREND_CACHE.get(pf)
        if c and now - c[0] < 300:
            items = c[1]
        else:
            items = await loop.run_in_executor(None, _fetch_platform, pf)
            if items:
                _TREND_CACHE[pf] = (now, items)
            elif c:
                items = c[1]
        result.append({
            "platform": pf,
            "label": TREND_LABELS.get(pf, pf),
            "items": items[:max(1, min(limit, 30))],
            "ok": bool(items),
        })
    return {"trends": result, "updated": int(now)}


SCHEDULE_FILE = OUTPUTS_DIR / "_schedule.json"
SCHEDULE_STATUSES = {"idea", "draft", "scheduled", "published"}
SCHEDULE_KINDS = {"content", "event"}

# _schedule.json 读-改-写串行化：发布回流（后台线程 _run_publish_bg + 事件循环上的
# api_publish 成功路径）与增删改端点并发时，不加锁会互相覆盖丢记录。
_SCHEDULE_LOCK = threading.Lock()


def _schedule_lock() -> threading.Lock:
    return _SCHEDULE_LOCK


def _read_schedule() -> list[dict]:
    if not SCHEDULE_FILE.is_file():
        return []
    try:
        d = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _write_schedule(items: list[dict]) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SCHEDULE_FILE)


class ScheduleItem(BaseModel):
    title: str
    date: str
    platform: str = ""
    time: str = ""
    status: str = "idea"
    note: str = ""
    kind: str = "content"          # content（内容/发布）| event（平台活动/节日/特殊日期）
    url: str = ""                  # 已发布内容链接（可选）
    source: str = "manual"         # manual | publish-page | chat | scheduler
    event_type: str = ""           # event 专属：节日/电商/平台活动/行业
    end_date: str = ""             # event 专属：活动区间结束日


@app.get("/api/schedule")
async def api_schedule_list():
    return _read_schedule()


@app.post("/api/schedule")
async def api_schedule_create(req: ScheduleItem):
    with _schedule_lock():
        items = _read_schedule()
        kind = req.kind if req.kind in SCHEDULE_KINDS else "content"
        st = req.status if req.status in SCHEDULE_STATUSES else "idea"
        item = {
            "id": uuid.uuid4().hex[:12],
            "title": req.title.strip() or ("未命名活动" if kind == "event" else "未命名"),
            "date": req.date,
            "platform": req.platform,
            "time": req.time,
            "status": st,
            "note": req.note,
            "kind": kind,
            "url": req.url,
            "source": req.source if req.source in {"manual", "publish-page", "chat", "scheduler"} else "manual",
            "event_type": req.event_type,
            "end_date": req.end_date,
        }
        items.append(item)
        _write_schedule(items)
    return item


@app.put("/api/schedule/{sid}")
async def api_schedule_update(sid: str, req: ScheduleItem):
    with _schedule_lock():
        items = _read_schedule()
        for it in items:
            if it.get("id") == sid:
                kind = req.kind if req.kind in SCHEDULE_KINDS else it.get("kind", "content")
                it.update({
                    "title": req.title.strip() or it.get("title", "未命名"),
                    "date": req.date,
                    "platform": req.platform,
                    "time": req.time,
                    "status": req.status if req.status in SCHEDULE_STATUSES else it.get("status", "idea"),
                    "note": req.note,
                    "kind": kind,
                    "url": req.url,
                    "event_type": req.event_type,
                    "end_date": req.end_date,
                })
                _write_schedule(items)
                return it
    raise HTTPException(404, "排期不存在")


@app.delete("/api/schedule/{sid}")
async def api_schedule_delete(sid: str):
    with _schedule_lock():
        items = _read_schedule()
        new = [it for it in items if it.get("id") != sid]
        if len(new) == len(items):
            raise HTTPException(404, "排期不存在")
        _write_schedule(new)
    return {"ok": True, "deleted": sid}


@app.get("/api/schedule/context")
async def api_schedule_context(days: int = 14):
    """规划摘要（发布节奏/断更缺口 + 待发排期 + 临近节点 + 建议）——薄封装 calendar_ops，
    前端页头「近期节点/建议」与 Agent 读回共用同一逻辑。失败返回空摘要不抛错。"""
    cmd = [sys.executable, str(SHARED_SCRIPTS / "calendar_ops.py"),
           "--data", str(SCHEDULE_FILE), "context", "--days", str(max(1, min(days, 90)))]
    try:
        # 冷启动要起一个 Python 子进程（最长 20s）：丢线程池，避免卡住事件循环
        proc = await asyncio.to_thread(
            subprocess.run, cmd, cwd=str(PROJECT_ROOT), env=_proxy_env(),
            capture_output=True, text=True, timeout=20)
        return json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
    except Exception:
        return {}


IDEAS_FILE = OUTPUTS_DIR / "_ideas.json"
IDEA_STATUSES = {"pending", "doing", "done"}


def _read_ideas() -> list[dict]:
    if not IDEAS_FILE.is_file():
        return []
    try:
        d = json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _write_ideas(items: list[dict]) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = IDEAS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(IDEAS_FILE)


class IdeaItem(BaseModel):
    title: str
    note: str = ""
    source: str = ""
    status: str = "pending"


@app.get("/api/ideas")
async def api_ideas_list():
    return _read_ideas()


@app.post("/api/ideas")
async def api_ideas_create(req: IdeaItem):
    items = _read_ideas()
    st = req.status if req.status in IDEA_STATUSES else "pending"
    item = {
        "id": uuid.uuid4().hex[:12],
        "title": req.title.strip() or "未命名选题",
        "note": req.note,
        "source": req.source,
        "status": st,
        "created": int(time.time()),
    }
    items.insert(0, item)
    _write_ideas(items)
    return item


@app.put("/api/ideas/{iid}")
async def api_ideas_update(iid: str, req: IdeaItem):
    items = _read_ideas()
    for it in items:
        if it.get("id") == iid:
            it.update({
                "title": req.title.strip() or it.get("title", "未命名选题"),
                "note": req.note,
                "source": req.source,
                "status": req.status if req.status in IDEA_STATUSES else it.get("status", "pending"),
            })
            _write_ideas(items)
            return it
    raise HTTPException(404, "选题不存在")


@app.delete("/api/ideas/{iid}")
async def api_ideas_delete(iid: str):
    items = _read_ideas()
    new = [it for it in items if it.get("id") != iid]
    if len(new) == len(items):
        raise HTTPException(404, "选题不存在")
    _write_ideas(new)
    return {"ok": True, "deleted": iid}


if __name__ == "__main__":
    import uvicorn
    from easel.runtime_env import web_environment

    os.environ.update(web_environment(dict(os.environ)))
    port = int(os.environ.get("EASEL_PORT", "7860"))
    proxy_url = os.environ.get("VSCODE_PROXY_URI", "").replace("{{port}}", str(port))
    print("\n  ✦ Easel Web")
    print(f"  http://localhost:{port}")
    if proxy_url:
        print(f"  {proxy_url}")
    print()
    # 监听 0.0.0.0（非仅回环）是为了兼容 Docker/远程容器端口转发访问 Easel 的场景；
    # local_write_guard/TrustedHostMiddleware 只挡浏览器发起的跨站请求，见其注释里的局限说明。
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
