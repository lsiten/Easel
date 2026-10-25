from __future__ import annotations

from pathlib import Path

import pytest

from easel import installation

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("runtime", ["codex", "opencode"])
def test_web_can_save_native_harness_from_panel(tmp_path, monkeypatch, runtime):
    """Web 面板能把某 Agent 底座（codex/opencode）切换为当前 runtime，且不碰 .env 机密。

    这是仍存在的设置行为：切换底座走 POST /api/settings/harness，
    describe() 判定本机是否已装对应 CLI，未装则 409 要求先在终端安装。
    """
    import asyncio
    import web.app as web

    monkeypatch.delenv("EASEL_AGENT_RUNTIME", raising=False)
    monkeypatch.delenv("EASEL_CHAT_TRANSPORT", raising=False)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path / "outputs")
    for name in ("_RUNNING_CHAT", "_BG_TASKS", "_DIRECT_CHAT_TASKS", "_HARNESS_TASKS"):
        monkeypatch.setattr(web, name, set())
    web.ENV_FILE.write_text("EASEL_CHAT_TRANSPORT=http\nOPENAI_API_KEY=keep-secret\n", encoding="utf-8")
    config = web.OUTPUTS_DIR / "_harness" / "config.json"
    config.unlink(missing_ok=True)
    installation.initialize_runtime(web.PROJECT_ROOT)
    assert web._harness_settings()["runtime"] == "api"
    # 被测的两个底座（codex/opencode）本机均视为已安装，允许切换。
    monkeypatch.setattr(web.harnesses, "describe",
                        lambda selected: {"id": selected, "installed": True})
    result = asyncio.run(
        web.api_harness_settings_save(
            web.HarnessSettingsRequest(runtime=runtime, model="native-model")))
    assert result["runtime"] == runtime
    assert result["locked"] is False
    assert web._harness_settings()["runtime"] == runtime
    assert "OPENAI_API_KEY=keep-secret" in web.ENV_FILE.read_text()
