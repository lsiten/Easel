"""easel doctor — 检查开发环境是否就绪。"""

from __future__ import annotations

import shutil
from pathlib import Path

from easel.direct_api import DirectAPIError, api_config, read_settings
from easel.browser import BrowserUnavailable, select_browser

# 项目根目录（Easel/）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[0;33m"
NC = "\033[0m"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = f"{GREEN}OK{NC}" if ok else f"{RED}FAIL{NC}"
    print(f"  {label:<40s} {status}")
    if not ok and detail:
        print(f"    └─ {detail}")
    return ok


def _python_version_ok() -> bool:
    import sys
    return sys.version_info >= (3, 10)


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def _venv_available() -> bool:
    return _module_available("venv")


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).is_file()
    except (ImportError, OSError, RuntimeError):
        return False


def _env_key_valid() -> bool:
    """Check .env 配置了可用的认证。

    以下任一通道满足即可：
    - 标准 API key：ANTHROPIC_API_KEY
    - Anthropic-compatible 服务：EASEL_LLM_API_KEY + EASEL_LLM_BASE_URL

    ping 才是权威连通性测试；这里只做静态配置存在性检查。
    """
    settings = read_settings(PROJECT_ROOT / ".env")
    if any(settings.get(name, "").strip() for name in (
            "EASEL_DIRECT_API_BASE_URL", "EASEL_DIRECT_API_MODEL", "EASEL_DIRECT_API_KEY")):
        try:
            api_config(settings)
            return True
        except DirectAPIError:
            return False
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return False

    # 认证变量 → 是否已填入非占位值
    auth_vars: dict[str, str] = {}
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key in (
                "ANTHROPIC_API_KEY", "EASEL_LLM_API_KEY", "EASEL_LLM_BASE_URL",
                "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "OPENAI_API_KEY", "OPENAI_BASE_URL",
                "OPENAI_MAAS_API_KEY", "OPENAI_MAAS_ENDPOINT",
            ):
                auth_vars[key] = value
    except OSError:
        return False

    def _set(name: str) -> bool:
        v = auth_vars.get(name, "")
        return bool(v) and "REPLACE_ME" not in v

    # 标准 key 通道
    if _set("ANTHROPIC_API_KEY"):
        return True
    # Anthropic-compatible 服务：key + base_url 同时配好
    if _set("EASEL_LLM_API_KEY") and _set("EASEL_LLM_BASE_URL"):
        return True
    if _set("ANTHROPIC_AUTH_TOKEN") and _set("ANTHROPIC_BASE_URL"):
        return True
    if _set("OPENAI_API_KEY"):
        return True
    if _set("OPENAI_MAAS_API_KEY") and _set("OPENAI_MAAS_ENDPOINT"):
        return True
    return False


def cmd_doctor(_args) -> int:
    print("Easel — 环境检查\n")
    all_ok = True
    settings = read_settings(PROJECT_ROOT / ".env")
    from easel import harnesses
    runtime = harnesses.settings(PROJECT_ROOT / "outputs", settings)["runtime"]
    alternate = runtime != "api"
    direct = runtime == "api"
    print("  对话模式：" + (runtime if alternate else "API 直连（仅聊天模式）"))
    if alternate:
        descriptor = harnesses.describe(runtime)
        all_ok &= _check(f"{runtime} CLI", descriptor["installed"], descriptor["install_hint"])
        print("  原生认证和模型可用性请运行 easel ping 验证；不读取或迁移凭证")
        direct = True

    # 1. Runtime prerequisites
    all_ok &= _check("Python >= 3.10", _python_version_ok(),
                      "请安装 Python 3.10 或更高版本")
    all_ok &= _check("Python venv module", _venv_available(),
                      "Debian/Ubuntu 请安装 python3-venv")
    all_ok &= _check("Node.js", shutil.which("node") is not None,
                      "opencode 底座与前端构建需要 Node.js：https://nodejs.org/")
    all_ok &= _check("FFmpeg", shutil.which("ffmpeg") is not None,
                      "媒体处理需要 FFmpeg；请安装后重试")

    for module in ("fastapi", "uvicorn", "sse_starlette", "multipart"):
        all_ok &= _check(f"Python package: {module}", _module_available(module),
                          "运行 pip install -e . 安装 Easel 运行依赖")

    frontend_ready = (PROJECT_ROOT / "web" / "frontend" / "dist" / "index.html").is_file()
    all_ok &= _check("Web frontend build", frontend_ready,
                      "运行 cd web/frontend && npm ci && npm run build")
    all_ok &= _check("Playwright Chromium", _chromium_available(),
                      "运行 python3 -m playwright install chromium")
    try:
        browser = select_browser()
        _check(f"平台浏览器: {browser.name}", True)
    except BrowserUnavailable as error:
        all_ok &= _check("平台浏览器: Ego / Chrome", False, str(error))

    if direct and not alternate:
        try:
            base, model, _key = api_config(settings)
            all_ok &= _check(f"API 配置 ({model})", True)
        except DirectAPIError as error:
            all_ok &= _check("API 配置", False, str(error))
    elif not alternate:
        # 3. .env file with valid key
        env_ok = _env_key_valid()
        all_ok &= _check(".env (API Key)", env_ok,
                          "填 ANTHROPIC_API_KEY，或 EASEL_LLM_API_KEY + EASEL_LLM_BASE_URL")

    # 3.5 本机 agent CLI：信息性提示，不计入 all_ok（装不装是用户的选择）。
    try:
        from easel.local_agents import summarize_local_agents
        la = summarize_local_agents()
        if la["installedCount"]:
            installed = ", ".join(str(a["label"]) for a in la["agents"] if a["installed"])
            _check("本机 agent CLI", True, f"检测到 {la['installedCount']} 个本机 agent CLI：{installed}")
    except Exception:  # noqa: BLE001  探测失败不阻塞 doctor
        pass

    # 3.6 OpenFilm（视频制作引擎 + Studio 可视化）：信息性提示，不计入 all_ok（可选子项目）。
    try:
        import importlib.util
        import sys as _sys
        shared = PROJECT_ROOT / "skills" / "shared" / "scripts"
        if str(shared) not in _sys.path:
            _sys.path.insert(0, str(shared))
        spec = importlib.util.spec_from_file_location("openfilm", shared / "openfilm.py")
        of_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(of_mod)
        j = of_mod._check_json(of_mod.capability())
        if j["available"]:
            detail = f"{j.get('openfilm')} via {j.get('runner')}"
            _check("OpenFilm（可选：视频制作 + Studio 可视化）", True, detail)
    except Exception:  # noqa: BLE001  探测失败不阻塞 doctor
        pass

    # 3.7 素材库（每账号素材源 → 按内容理解整理 → 供视频/文章创作取用）：信息性提示，不计入 all_ok。
    try:
        import importlib.util
        import sys as _sys
        shared = PROJECT_ROOT / "skills" / "shared" / "scripts"
        if str(shared) not in _sys.path:
            _sys.path.insert(0, str(shared))
        spec = importlib.util.spec_from_file_location("assets", shared / "assets.py")
        as_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(as_mod)
        j = as_mod.check_json(None)
        if j.get("registered_all"):
            detail = f"已登记 {j['registered_all']} 个画像素材源"
            _check("素材库（可选：每账号素材整理）", True, detail)
    except Exception:  # noqa: BLE001  探测失败不阻塞 doctor
        pass

    # Key project files
    key_files = [
        ("skills/builtin/", PROJECT_ROOT / "skills" / "builtin"),
    ]
    for label, path in key_files:
        all_ok &= _check(label, path.exists())

    print()
    if all_ok:
        print(f"{GREEN}✓ 环境就绪{NC} — 运行 python -m easel ping 验证连通性")
    else:
        print(f"{YELLOW}⚠ 有未满足项{NC} — 请按上述提示修复后重试")

    return 0 if all_ok else 1
