import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
import app as web


@pytest.fixture(autouse=True)
def isolated_inflight(monkeypatch):
    monkeypatch.setattr(web, "_analytics_inflight", {})


def test_concurrent_refreshes_share_one_fetch(monkeypatch):
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def fetch(platform):
            calls.append(platform)
            started.set()
            await finish.wait()
            return {"platform": platform}

        monkeypatch.setattr(web, "_fetch_analytics", fetch)
        first = asyncio.create_task(web.api_analytics("xiaohongshu"))
        await started.wait()
        second = asyncio.create_task(web.api_analytics("xiaohongshu"))
        await asyncio.sleep(0)
        finish.set()
        assert await first == await second == {"platform": "xiaohongshu"}
        assert calls == ["xiaohongshu"]
        await asyncio.sleep(0)
        assert web._analytics_inflight == {}
        assert await web.api_analytics("xiaohongshu") == {"platform": "xiaohongshu"}
        assert calls == ["xiaohongshu", "xiaohongshu"]

    asyncio.run(scenario())


def test_cancelled_client_does_not_cancel_shared_fetch(monkeypatch):
    async def scenario():
        finish = asyncio.Event()
        async def fetch(platform):
            await finish.wait()
            return {"platform": platform}

        monkeypatch.setattr(web, "_fetch_analytics", fetch)
        client = asyncio.create_task(web.api_analytics("xiaohongshu"))
        await asyncio.sleep(0)
        client.cancel()
        with pytest.raises(asyncio.CancelledError):
            await client
        assert not web._analytics_inflight["xiaohongshu"].cancelled()
        other = asyncio.create_task(web.api_analytics("xiaohongshu"))
        finish.set()
        assert await other == {"platform": "xiaohongshu"}

    asyncio.run(scenario())


def test_failed_refresh_can_be_retried(monkeypatch):
    async def scenario():
        async def fetch(platform):
            raise HTTPException(502, "Unavailable")

        monkeypatch.setattr(web, "_fetch_analytics", fetch)
        for attempt in range(2):
            with pytest.raises(HTTPException):
                await web.api_analytics("xiaohongshu")
            await asyncio.sleep(0)
            assert web._analytics_inflight == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("code,payload,status", [
    (0, {"loggedIn": True, "followers": None, "likes": None, "metrics": [], "notes": []}, 502),
    (1, {"loggedIn": True, "followers": 7}, 502),
    (0, {"error": "Platform maintenance"}, 502),
    (0, {"loggedIn": True, "followers": 0}, None),
    (0, {"loggedIn": False}, None),
])
def test_failed_or_empty_payload_is_not_success(monkeypatch, code, payload, status):
    monkeypatch.setattr(web, "_run_analytics_command", lambda *args: subprocess.CompletedProcess(
        [], code, json.dumps(payload), ""))
    if status:
        with pytest.raises(HTTPException) as error:
            asyncio.run(web._fetch_analytics("xiaohongshu"))
        assert error.value.status_code == status
    else:
        assert asyncio.run(web._fetch_analytics("xiaohongshu")) == payload


def test_fetch_propagates_actionable_script_error(monkeypatch):
    monkeypatch.setattr(web, "_run_analytics_command", lambda *args: subprocess.CompletedProcess(
        [], 1, "", "ERROR: 小红书登录目录正被占用"))
    with pytest.raises(HTTPException, match="登录目录正被占用"):
        asyncio.run(web._fetch_analytics("xiaohongshu"))


def test_fetch_reports_bounded_timeout(monkeypatch):
    runner = Mock(side_effect=subprocess.TimeoutExpired("fetch", 90))
    monkeypatch.setattr(web, "_run_analytics_command", runner)
    with pytest.raises(HTTPException) as error:
        asyncio.run(web._fetch_analytics("xiaohongshu"))
    assert error.value.status_code == 504
    assert runner.call_args.args[2] == 90
    assert runner.call_args.args[1]["PYTHONIOENCODING"] == "utf-8"
    assert runner.call_args.args[1]["PYTHONUTF8"] == "1"


def test_subprocess_timeout_stops_browser_tree(monkeypatch):
    process = Mock(pid=123456)
    process.communicate.side_effect = [subprocess.TimeoutExpired("fetch", 1), ("", "")]
    popen = Mock(return_value=process)
    monkeypatch.setattr(web.subprocess, "Popen", popen)
    if os.name == "nt":
        killer = Mock()
        monkeypatch.setattr(web.subprocess, "run", killer)
    else:
        killer = Mock()
        monkeypatch.setattr(web.os, "killpg", killer)
    with pytest.raises(subprocess.TimeoutExpired):
        web._run_analytics_command(["python", "fetch"], {}, 1)
    assert popen.call_args.kwargs["encoding"] == "utf-8"
    assert popen.call_args.kwargs["start_new_session"] is (os.name != "nt")
    if os.name == "nt":
        assert killer.call_args.args[0] == ["taskkill", "/PID", "123456", "/T", "/F"]
    else:
        killer.assert_called_once_with(123456, signal.SIGKILL)
    process.kill.assert_called_once()
    assert process.communicate.call_count == 2


@pytest.mark.parametrize("platform", ["csdn", "juejin"])
def test_newly_adapted_platforms_are_fetchable_not_404(monkeypatch, platform):
    # csdn/juejin 已进入 ANALYTICS_PLATFORMS（归因层适配门）：api_analytics 不再 404，
    # 而是像其它平台一样进入 _fetch_analytics（csdn/juejin 走 account_stats 通用抓取路径）。
    async def scenario():
        async def fetch(platform, persona=""):
            return {"platform": platform, "loggedIn": False}
        monkeypatch.setattr(web, "_fetch_analytics", fetch)
        out = await web.api_analytics(platform)
        assert out == {"platform": platform, "loggedIn": False}
    asyncio.run(scenario())


def test_newly_adapted_platforms_are_in_analytics_set():
    assert {"csdn", "juejin"} <= set(web.ANALYTICS_PLATFORMS)
    # 列表接口同样按画像过滤，csdn/juejin 已在 LOGIN_RUNNERS，会被列出
    for pf in ("csdn", "juejin"):
        assert pf in web.LOGIN_RUNNERS
        assert pf in web.ANALYTICS_PLATFORMS


def test_graceful_degradation_warning_is_not_502(monkeypatch):
    # CSDN 被 WAF 拦截：loggedIn=True 但全空 + warning → 不 502，返回结果让前端显示空/—+提示，
    # 绝不把 WAF 页里的非本人数据（账号管理规范/版权申诉）当成本人数据。
    monkeypatch.setattr(web, "_run_analytics_command", lambda *args: subprocess.CompletedProcess(
        [], 0, json.dumps({
            "platform": "csdn", "loggedIn": True, "followers": None, "likes": None,
            "following": None, "posts": None, "metrics": [], "notes": [],
            "warning": "数据页被平台拦截（WAF/403/404），已跳过，未显示非本人数据",
        }), ""))
    out = asyncio.run(web._fetch_analytics("csdn"))
    assert out["loggedIn"] is True
    assert out["warning"].startswith("数据页被平台拦截")
    assert all(out.get(k) is None for k in ("followers", "likes", "following", "posts"))


def test_platform_profile_url_from_persona_is_passed_to_fetch(monkeypatch):
    # 主修复：csdn/juejin 应把画像 platforms.md 里配好的「本人主页」透传给 account_stats（--profile-url），
    # 直连本人主页、绕过会误抓推荐作者的自动解析。
    captured = {}
    def _runner(cmd, env, timeout):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess([], 0, json.dumps({
            "platform": "csdn", "loggedIn": False, "followers": None, "likes": None,
            "following": None, "posts": None, "metrics": [], "notes": [],
        }), "")
    monkeypatch.setattr(web, "_run_analytics_command", _runner)
    out = asyncio.run(web._fetch_analytics("csdn", "水獭比特"))
    assert out["loggedIn"] is False
    assert "--profile-url" in captured["cmd"]
    assert "https://blog.csdn.net/Lsiten" in captured["cmd"]
