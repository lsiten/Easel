"""公众号重复登录及异常状态回归测试，不启动真实浏览器。"""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pytest

import web.app as web

ROOT = Path(__file__).resolve().parents[1]


def test_repeated_start_preserves_existing_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock()
    process.poll.return_value = None
    qr = tmp_path / "wechat-oa-mp.png"
    qr.write_bytes(b"existing-qr")
    (tmp_path / "wechat-oa-mp.json").write_text('{"state":"qr_ready"}', encoding="utf-8")
    monkeypatch.setattr(web, "LOGIN_DIR", tmp_path)
    monkeypatch.setattr(web, "LOGIN_PROCESSES", {"_general::wechat-oa-mp": process})
    launch = Mock(side_effect=AssertionError("An active login must not launch a second browser"))
    monkeypatch.setattr(web.subprocess, "Popen", launch)

    for _ in range(2):
        result = asyncio.run(web.api_mp_login_start("wechat-oa"))
        assert result["mode"] == "qr"
        assert result["state"] == "qr_ready"
        assert result["qr"] == "_login/wechat-oa-mp.png"
    assert qr.read_bytes() == b"existing-qr"
    launch.assert_not_called()


@pytest.mark.parametrize("persona", ["", "named-account"])
def test_shutdown_reaps_login_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persona: str,
) -> None:
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(web, "LOGIN_DIR", tmp_path)
    monkeypatch.setattr(web, "profile_exists", lambda name: name == "named-account")
    monkeypatch.setattr(web, "LOGIN_PROCESSES", {f"{persona or '_general'}::wechat-oa-mp": process})

    web._stop_mp_login_on_shutdown()

    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
    assert web.LOGIN_PROCESSES == {}
    marker = tmp_path / persona / "wechat-oa-mp.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["state"] == "expired"


class MpLoginTests(unittest.TestCase):
    def test_browser_failure_writes_error_state(self):
        script = ROOT / 'skills/shared/scripts/weixin_mp_stats.py'
        spec = importlib.util.spec_from_file_location('mp_login_under_test', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            args = Mock(status_file=str(Path(d) / 'status.json'))
            with patch.object(module, '_run_login', side_effect=RuntimeError('browser launch failed')):
                self.assertEqual(module.cmd_login(args), 1)
            self.assertEqual(module.login_state.read_status(args.status_file)['state'], 'error')

    def test_weixin_resolve_content_image_maps_api_media(self):
        script = ROOT / 'skills/shared/scripts/weixin_mp_stats.py'
        spec = importlib.util.spec_from_file_location('mp_stats_test', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            tmp_path = Path(d)
            module.PROJECT_ROOT = tmp_path
            image = tmp_path / "outputs" / "gallery" / "a.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            html = tmp_path / "outputs" / "articles" / "a.html"
            html.parent.mkdir(parents=True)
            html.write_text("<p></p>", encoding="utf-8")
            self.assertEqual(module.resolve_content_image("/api/media/gallery/a.png", html), image.resolve())
            self.assertEqual(module.resolve_content_image("https://127.0.0.1:7860/api/media/gallery/a.png", html), image.resolve())
            self.assertIsNone(module.resolve_content_image("https://cdn.example/x.png", html))
            self.assertIsNone(module.resolve_content_image("/api/media/_login/secret.png", html))

    def test_weixin_profile_lock_rejects_second_holder(self):
        script = ROOT / 'skills/shared/scripts/weixin_mp_stats.py'
        spec = importlib.util.spec_from_file_location('mp_stats_test2', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as d:
            prof = Path(d) / "WeixinMpProfile"
            holder = module._lock_profile(prof)
            try:
                with self.assertRaises(RuntimeError):
                    module._lock_profile(prof)
            finally:
                module._unlock_profile(holder)

if __name__ == '__main__':
    unittest.main()

