"""Select Ego before Chrome for persistent platform browser sessions."""
from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

if sys.version_info >= (3, 11):
    from typing import assert_never
else:
    from typing_extensions import assert_never

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Playwright, ProxySettings, ViewportSize


class BrowserOptions(TypedDict, total=False):
    headless: bool
    locale: str
    args: list[str]
    viewport: ViewportSize
    proxy: ProxySettings


class EgoOptions(BrowserOptions):
    executable_path: str


class BrowserUnavailable(RuntimeError):
    """An installed browser failed, or neither supported browser was found."""


@dataclass(frozen=True, slots=True)
class BrowserChoice:
    name: Literal["ego", "chrome"]
    executable: Path


def _candidates(name: Literal["ego", "chrome"]) -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        app = {"ego": "ego lite", "chrome": "Google Chrome"}[name]
        binary = app
        return [base / f"{app}.app" / "Contents" / "MacOS" / binary
                for base in (home / "Applications", Path("/Applications"))]
    if sys.platform == "win32":
        locations = [Path(os.environ.get(key, default)) for key, default in (
            ("LOCALAPPDATA", str(home / "AppData" / "Local")),
            ("PROGRAMFILES", r"C:\Program Files"),
            ("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))]
        relatives = {
            "ego": ("ego lite/ego.exe", "Programs/ego lite/ego.exe", "ego/ego.exe", "Programs/ego lite/ego lite.exe"),
            "chrome": ("Google/Chrome/Application/chrome.exe",),
        }[name]
        return [base / relative for base in locations for relative in relatives]
    return [Path(path) for path in {
        "ego": ("/opt/ego/ego", "/opt/ego-lite/ego", "/usr/bin/ego-lite"),
        "chrome": ("/opt/google/chrome/chrome", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"),
    }[name]]


def _executable(name: Literal["ego", "chrome"]) -> Path | None:
    override = os.environ.get(f"EASEL_{name.upper()}_EXECUTABLE", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise BrowserUnavailable(f"{name} 的浏览器路径不可执行；请检查 EASEL_{name.upper()}_EXECUTABLE")
        return path.resolve()
    commands = {"ego": ("ego-lite", "ego"), "chrome": ("google-chrome", "google-chrome-stable", "chrome")}[name]
    candidates = _candidates(name)
    candidates.extend(Path(found) for command in commands if (found := shutil.which(command)))
    if name == "ego" and (cli := shutil.which("ego-browser")):
        # macOS CLI links into the versioned framework inside its owning app.
        app = next((parent for parent in Path(cli).resolve().parents if parent.suffix == ".app"), None)
        if app:
            candidates.append(app / "Contents" / "MacOS" / "ego lite")
        else:
            candidates.extend(Path(cli).resolve().parent / filename for filename in ("ego.exe", "ego lite.exe"))
    for path in candidates:
        if path.is_file():
            if not os.access(path, os.X_OK):
                raise BrowserUnavailable(f"已找到 {name}，但浏览器不可执行；请检查安装或文件权限")
            return path.resolve()
    return None


def select_browser() -> BrowserChoice:
    """Detect at launch time so installation changes need no settings migration."""
    ego = _executable("ego")
    if ego:
        return BrowserChoice("ego", ego)
    if shutil.which("ego-browser"):
        raise BrowserUnavailable("已安装 Ego CLI，但未找到浏览器；请设置 EASEL_EGO_EXECUTABLE 指向 Ego 浏览器")
    chrome = _executable("chrome")
    if chrome:
        return BrowserChoice("chrome", chrome)
    raise BrowserUnavailable("未安装 Ego 或 Chrome；请安装 Chrome，或设置 EASEL_CHROME_EXECUTABLE")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _ego_context(playwright: Playwright, profile: Path,
                 options: EgoOptions) -> BrowserContext:
    """Own an isolated Ego process; Ego supports TCP CDP rather than PW pipes."""
    profile.mkdir(parents=True, exist_ok=True)
    with ExitStack() as cleanup:
        lock = cleanup.enter_context((profile / ".easel-browser.lock").open("a+b"))
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                lock.write(b"\0")
                lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise BrowserUnavailable("该平台的 Ego 账号目录正在使用，请等待当前操作结束") from error
        port_file = profile / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        server = hashlib.sha256(str(profile).encode()).hexdigest()[:16]
        args = [options["executable_path"], *options.get("args", []),
                f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0", f"--ego-server-name=easel-{server}",
                "--no-first-run", "--no-default-browser-check"]
        if options.get("headless", True):
            args.append("--headless=new")
        if locale := options.get("locale"):
            args.extend([f"--lang={locale}", f"--accept-lang={locale}"])
        if proxy := options.get("proxy"):
            if proxy.get("username") or proxy.get("password"):
                raise BrowserUnavailable("Ego 的持久化连接暂不支持带认证的代理，请使用本机无认证代理")
            args.append(f"--proxy-server={proxy['server']}")
            if bypass := proxy.get("bypass"):
                args.append(f"--proxy-bypass-list={bypass}")
        process = subprocess.Popen([*args, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cleanup.callback(_stop, process)
        # Several legacy commands exit via SystemExit before closing their context.
        def stop_on_exit() -> None:
            _stop(process)
        atexit.register(stop_on_exit)
        cleanup.callback(atexit.unregister, stop_on_exit)
        deadline = time.monotonic() + 15
        endpoint = ""
        while process.poll() is None and time.monotonic() < deadline:
            if port_file.is_file():
                lines = port_file.read_text(encoding="utf-8").splitlines()
                if len(lines) >= 2 and lines[0].isdigit() and lines[1].startswith("/devtools/browser/"):
                    endpoint = f"ws://127.0.0.1:{int(lines[0])}{lines[1]}"
                    break
            time.sleep(0.05)
        if not endpoint:
            raise BrowserUnavailable("Ego 启动失败或连接超时；请检查 Ego 安装与账号目录，没有切换到 Chrome")
        connection = playwright.chromium.connect_over_cdp(endpoint, timeout=15000)
        cleanup.callback(connection.close)
        state_file = profile / "easel-storage-state.json"
        context = connection.new_context(
            storage_state=state_file if state_file.is_file() else None,
            viewport=options.get("viewport"), locale=options.get("locale"), proxy=options.get("proxy"))
        cleanup.callback(context.close)
        owned = cleanup.pop_all()

    closed = False

    def close(*, reason: str | None = None) -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            # Persist before shutdown: Ego's CDP profile may not flush network cookies.
            with tempfile.NamedTemporaryFile(dir=profile, prefix=".easel-state-", delete=False) as temporary:
                temporary_path = Path(temporary.name)
            try:
                context.storage_state(path=temporary_path, indexed_db=True)
                os.replace(temporary_path, state_file)
            finally:
                temporary_path.unlink(missing_ok=True)
        finally:
            owned.close()

    context.close = close
    return context


def launch_persistent_context(playwright: Playwright, profile: str,
                              options: BrowserOptions) -> BrowserContext:
    """Preserve platform/persona isolation and never retry a failed Ego as Chrome."""
    browser = select_browser()
    print(f"浏览器：{browser.name}（{browser.executable}）", file=sys.stderr)
    match browser.name:
        case "ego":
            return _ego_context(playwright, Path(profile).resolve() / "Ego",
                                {**options, "executable_path": str(browser.executable)})
        case "chrome":
            return playwright.chromium.launch_persistent_context(
                profile, executable_path=str(browser.executable), **options)
        case unreachable:
            assert_never(unreachable)


def main() -> int:
    """Read-only browser detection for installers and environment diagnostics."""
    try:
        browser = select_browser()
    except BrowserUnavailable as error:
        print(str(error), file=sys.stderr)
        return 3
    print(f"{browser.name}: {browser.executable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
