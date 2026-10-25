"""安装脚本动作流的密钥脱敏回归。

setup.sh 会把配置动作打进 CI 日志（emit_action）。整块写配置时密钥藏在 value 里，
早期版本只按 key 名脱敏，会把 apiKey 明文打进日志。这里钉住 emit_action 必须按
value 也脱敏。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_TEXT = (PROJECT_ROOT / "setup.sh").read_text(encoding="utf-8")


def test_emit_action_redacts_secrets_in_value() -> None:
    """动作流会进 CI 日志；整块写配置时密钥藏在 value 里，必须按 value 也脱敏。"""
    block = SETUP_TEXT[SETUP_TEXT.index("emit_action() {"):]
    block = block[: block.index("\n}\n") + 3]
    assert 'case "$value" in' in block, "emit_action 必须同时按 value 脱敏"
    assert "apiKey" in block
