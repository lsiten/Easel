"""快速路径 _account_logged_in 的浏览器标识校验回归测试。

背景：Ego/Chrome 是隔离 profile，切换浏览器后旧登录标记可能失效。
- 新标记（带 browser 字段）：与当前活动浏览器不符 → 判未登录（浏览器切换，profile 隔离）。
- 历史标记（无 browser 字段）：无法廉价判定是否早于切换 → 信任成功标记，交由 whoami 真校验
  在拿到「确定」结论时重新打标/清除来收敛；不得硬判未登录（否则「实际已登录」或
  「校验 inconclusive（网络/风控）」时会永久误报未登录且无法自愈）。

使用通用画像（persona=""），_login_dir 直接落到 LOGIN_DIR，避免命名画像 profile 校验。
"""
import json

import pytest

import web.app as web


@pytest.fixture
def account_state(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "LOGIN_DIR", tmp_path)
    monkeypatch.setattr(web, "_active_browser_name", lambda: "ego")
    return tmp_path


def _write(tmp_path, *, state="success", browser=None):
    data = {"state": state, "message": "acct", "qr": "", "ts": 1}
    if browser:
        data["browser"] = browser
    (tmp_path / "xiaohongshu.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _check(tmp_path):
    return web._account_logged_in("xiaohongshu", web.LOGIN_RUNNERS["xiaohongshu"], "")


def test_legacy_marker_trusted_as_logged_in(account_state):
    # 历史标记（无 browser 字段）+ 可判定活动浏览器 → 信任，不再永久误报未登录
    _write(account_state)
    assert _check(account_state) is True


def test_new_marker_mismatch_active_browser_is_not_logged_in(account_state):
    _write(account_state, browser="chrome")
    assert _check(account_state) is False


def test_new_marker_matches_active_browser_is_logged_in(account_state):
    _write(account_state, browser="ego")
    assert _check(account_state) is True


def test_non_success_marker_is_not_logged_in(account_state):
    _write(account_state, state="expired", browser="ego")
    assert _check(account_state) is False


def test_legacy_marker_still_trusted_when_browser_undetectable(account_state, monkeypatch):
    monkeypatch.setattr(web, "_active_browser_name", lambda: None)
    _write(account_state)
    assert _check(account_state) is True
