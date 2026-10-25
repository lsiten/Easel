"""Browser selection and process ownership, without using real account sessions."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from easel import browser


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.delenv("EASEL_EGO_EXECUTABLE", raising=False)
    monkeypatch.delenv("EASEL_CHROME_EXECUTABLE", raising=False)
    monkeypatch.setattr(browser.shutil, "which", lambda command: None)
    monkeypatch.setattr(browser, "_candidates", lambda name: [tmp_path / name])

    def install(name):
        path = tmp_path / name
        path.write_text("browser fixture", encoding="utf-8")
        path.chmod(0o755)
        return path.resolve()

    return install


def test_ego_wins_when_both_browsers_are_installed(installed):
    # Given
    ego = installed("ego")
    installed("chrome")
    # When
    choice = browser.select_browser()
    # Then
    assert choice == browser.BrowserChoice("ego", ego)


def test_chrome_is_selected_when_ego_is_absent(installed):
    # Given
    chrome = installed("chrome")
    # When
    choice = browser.select_browser()
    # Then
    assert choice == browser.BrowserChoice("chrome", chrome)


def test_missing_browsers_report_installation_error(installed):
    # Given / When / Then
    with pytest.raises(browser.BrowserUnavailable):
        browser.select_browser()


def test_ego_cli_without_resolvable_app_is_not_mistaken_for_absence(installed, monkeypatch):
    # Given
    installed("chrome")
    monkeypatch.setattr(browser.shutil, "which", lambda command: "/custom/ego-browser" if command == "ego-browser" else None)
    # When / Then
    with pytest.raises(browser.BrowserUnavailable):
        browser.select_browser()


def test_invalid_explicit_ego_path_does_not_fall_back(installed, monkeypatch, tmp_path):
    # Given
    installed("chrome")
    monkeypatch.setenv("EASEL_EGO_EXECUTABLE", str(tmp_path / "missing"))
    # When / Then
    with pytest.raises(browser.BrowserUnavailable):
        browser.select_browser()


def test_broken_ego_permissions_are_reported_instead_of_switching(installed, monkeypatch):
    # Given
    ego = installed("ego")
    installed("chrome")
    original = browser.os.access
    monkeypatch.setattr(browser.os, "access", lambda path, mode: False if Path(path) == ego else original(path, mode))
    # When / Then
    with pytest.raises(browser.BrowserUnavailable):
        browser.select_browser()


def test_custom_ego_installation_is_used(installed, monkeypatch):
    # Given
    ego = installed("custom ego")
    installed("chrome")
    monkeypatch.setenv("EASEL_EGO_EXECUTABLE", str(ego))
    # When
    choice = browser.select_browser()
    # Then
    assert choice.executable == ego
    assert choice.name == "ego"


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_standard_browser_locations(platform, tmp_path, monkeypatch):
    # Given
    monkeypatch.setattr(browser.sys, "platform", platform)
    monkeypatch.setattr(browser.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    # When
    ego, chrome = browser._candidates("ego"), browser._candidates("chrome")
    # Then
    assert ego and chrome
    assert set(ego).isdisjoint(chrome)
    if platform == "darwin":
        assert tmp_path / "Applications/ego lite.app/Contents/MacOS/ego lite" in ego
        assert Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") in chrome
    elif platform == "win32":
        assert tmp_path / "Local/Programs/ego lite/ego.exe" in ego
        assert tmp_path / "Local/Google/Chrome/Application/chrome.exe" in chrome
    else:
        assert Path("/opt/ego/ego") in ego
        assert Path("/opt/google/chrome/chrome") in chrome


def test_ego_cli_symlink_finds_custom_macos_app(installed, tmp_path, monkeypatch):
    # Given
    app = tmp_path / "custom/ego lite.app/Contents"
    executable = app / "MacOS/ego lite"
    executable.parent.mkdir(parents=True)
    executable.write_text("browser fixture", encoding="utf-8")
    executable.chmod(0o755)
    cli = app / "Frameworks/ego Framework.framework/Versions/1/Helpers/ego-browser"
    cli.parent.mkdir(parents=True)
    cli.touch()
    link = tmp_path / "ego-browser"
    link.symlink_to(cli)
    monkeypatch.setattr(browser.shutil, "which", lambda command: str(link) if command == "ego-browser" else None)
    # When
    choice = browser.select_browser()
    # Then
    assert choice == browser.BrowserChoice("ego", executable.resolve())


def test_chrome_launch_preserves_profile_proxy_and_viewport(installed, tmp_path):
    # Given
    chrome = installed("chrome")
    launch = Mock()
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    options = {"headless": False, "locale": "zh-CN", "args": ["--no-proxy-server"],
               "viewport": {"width": 1440, "height": 900}, "proxy": {"server": "http://localhost:8080"}}
    # When
    context = browser.launch_persistent_context(playwright, str(tmp_path / "persona/platform"), options)
    # Then
    assert context is launch.return_value
    launch.assert_called_once_with(str(tmp_path / "persona/platform"), executable_path=str(chrome), **options)


def test_failed_ego_process_releases_lock_without_starting_chrome(installed, tmp_path, monkeypatch):
    # Given
    installed("ego")
    installed("chrome")
    native_launch = Mock()
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=native_launch))
    process = Mock()
    process.poll.return_value = 1
    monkeypatch.setattr(browser.subprocess, "Popen", Mock(return_value=process))
    profile = str(tmp_path / "platform")
    # When / Then: retry also reaches startup, proving the failed attempt released its lock.
    for _ in range(2):
        with pytest.raises(browser.BrowserUnavailable):
            browser.launch_persistent_context(playwright, profile, {"headless": True})
    assert browser.subprocess.Popen.call_count == 2
    native_launch.assert_not_called()


def test_ego_connects_only_to_its_profile_and_close_reaps_process(installed, tmp_path, monkeypatch):
    # Given
    ego = installed("ego")
    process = Mock()
    process.poll.return_value = None
    def exited(timeout):
        process.poll.return_value = 0
        return 0
    process.wait.side_effect = exited
    profile = tmp_path / "persona" / "platform"
    (profile / "Ego").mkdir(parents=True)
    (profile / "Ego" / "DevToolsActivePort").write_text("9999\n/devtools/browser/stale", encoding="utf-8")

    def start(args, **kwargs):
        assert not (profile / "Ego" / "DevToolsActivePort").exists()
        (profile / "Ego" / "DevToolsActivePort").write_text("12345\n/devtools/browser/owned", encoding="utf-8")
        return process

    start_process = Mock(side_effect=start)
    monkeypatch.setattr(browser.subprocess, "Popen", start_process)
    native_close = Mock()
    context = SimpleNamespace(close=native_close, storage_state=Mock())
    new_context = Mock(return_value=context)
    connection = SimpleNamespace(new_context=new_context, close=Mock())
    connect = Mock(return_value=connection)
    native_launch = Mock()
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect, launch_persistent_context=native_launch))
    # When
    returned = browser.launch_persistent_context(playwright, str(profile), {
        "headless": True, "locale": "zh-CN", "viewport": {"width": 1280, "height": 900},
        "proxy": {"server": "http://localhost:8080", "bypass": "localhost"}})
    returned.close()
    returned.close()
    # Then
    args = start_process.call_args.args[0]
    assert args[0] == str(ego)
    assert f"--user-data-dir={profile / 'Ego'}" in args
    assert "--headless=new" in args
    assert "--proxy-server=http://localhost:8080" in args
    assert "--proxy-bypass-list=localhost" in args
    connect.assert_called_once_with("ws://127.0.0.1:12345/devtools/browser/owned", timeout=15000)
    new_context.assert_called_once_with(storage_state=None, locale="zh-CN",
        viewport={"width": 1280, "height": 900}, proxy={"server": "http://localhost:8080", "bypass": "localhost"})
    assert context.storage_state.call_args.kwargs["indexed_db"] is True
    assert (profile / "Ego/easel-storage-state.json").is_file()
    native_close.assert_called_once()
    connection.close.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
    process.terminate.assert_called_once()
    native_launch.assert_not_called()


def test_interrupted_ego_connection_reaps_process_and_releases_lock(installed, tmp_path, monkeypatch):
    # Given
    installed("ego")
    process = Mock()
    process.poll.return_value = None
    profile = tmp_path / "platform"

    def start(args, **kwargs):
        (profile / "Ego/DevToolsActivePort").write_text("12345\n/devtools/browser/owned", encoding="utf-8")
        return process

    monkeypatch.setattr(browser.subprocess, "Popen", Mock(side_effect=start))
    connect = Mock(side_effect=KeyboardInterrupt)
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    # When / Then: both attempts must reach CDP rather than leave a locked directory.
    for _ in range(2):
        with pytest.raises(KeyboardInterrupt):
            browser.launch_persistent_context(playwright, str(profile), {"headless": True})
    assert connect.call_count == process.terminate.call_count == 2


@pytest.mark.skipif(os.environ.get("EASEL_RUN_BROWSER_QA") != "1", reason="explicit local Ego integration QA")
def test_ego_network_login_state_survives_close_and_reopen(tmp_path):
    # Given: a local login-like server; no real accounts, cookies or platform requests.
    from playwright.sync_api import sync_playwright
    if browser.select_browser().name != "ego":
        pytest.skip("Ego is required for this integration regression")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "easel_session=saved; Max-Age=3600; Path=/")
            self.end_headers()
            self.wfile.write(b"<title>Easel local login</title>ready")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            context = browser.launch_persistent_context(playwright, str(tmp_path), {"headless": True})
            page = context.new_page()
            origin = f"http://127.0.0.1:{server.server_port}"
            page.goto(origin)
            page.evaluate("localStorage.setItem('easel_user', 'saved')")
            page.evaluate("""() => new Promise((resolve, reject) => {
                const request = indexedDB.open('easel_auth', 1);
                request.onupgradeneeded = () => request.result.createObjectStore('session');
                request.onerror = () => reject(request.error);
                request.onsuccess = () => {
                    const database = request.result;
                    const transaction = database.transaction('session', 'readwrite');
                    transaction.objectStore('session').put('saved', 'user');
                    transaction.oncomplete = () => { database.close(); resolve(true); };
                    transaction.onerror = () => reject(transaction.error);
                };
            })""")
            # When: finish the first operation and open a fresh browser process.
            context.close()
            if os.name != "nt":
                assert (tmp_path / "Ego/easel-storage-state.json").stat().st_mode & 0o777 == 0o600
            reopened = browser.launch_persistent_context(playwright, str(tmp_path), {"headless": True})
            try:
                cookies = reopened.cookies()
                new_page = reopened.new_page()
                new_page.goto(origin)
                # Then: both the server cookie and origin storage survived.
                assert any(cookie["name"] == "easel_session" for cookie in cookies)
                assert new_page.evaluate("localStorage.getItem('easel_user')") == "saved"
                assert new_page.evaluate("""() => new Promise((resolve, reject) => {
                    const request = indexedDB.open('easel_auth', 1);
                    request.onerror = () => reject(request.error);
                    request.onsuccess = () => {
                        const database = request.result;
                        if (!database.objectStoreNames.contains('session')) {
                            database.close(); resolve(null); return;
                        }
                        const value = database.transaction('session').objectStore('session').get('user');
                        value.onsuccess = () => { database.close(); resolve(value.result); };
                        value.onerror = () => reject(value.error);
                    };
                })""") == "saved"
            finally:
                reopened.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
