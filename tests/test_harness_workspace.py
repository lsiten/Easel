from pathlib import Path
from dataclasses import replace
import asyncio
import json
import os
import sys

import pytest

from easel.harnesses.base import HarnessError, RunRequest
from easel.harnesses.workspace import harness_instructions, workspace_documents
from easel.harnesses.rpc import close_sessions
from easel.harnesses.codex import CodexAdapter
from easel.harnesses.acp import ACPAdapter


def workspace_at(root):
    source = root / "agents" / "workspace"
    source.mkdir(parents=True)
    (source / "AGENTS.md").write_text("共享业务规则", encoding="utf-8")
    (source / "SOUL.md").write_text("共享人格", encoding="utf-8")
    (source / "EXTRA.md").write_text("额外说明", encoding="utf-8")
    return source


@pytest.mark.parametrize("runtime", ["codex", "opencode", "acp"])
def test_native_harnesses_receive_all_shared_documents_and_actual_runtime(tmp_path, runtime):
    workspace_at(tmp_path)
    request = RunRequest("hello", tmp_path, {"EASEL_AGENT_RUNTIME": runtime})
    instructions = harness_instructions(request)
    assert all(value in instructions for value in ("共享业务规则", "共享人格", "额外说明"))
    assert instructions.index("SOUL.md") < instructions.index("AGENTS.md")
    assert f"当前执行引擎：{runtime}" in instructions
    assert str(tmp_path.resolve()) in instructions
    assert str(tmp_path / "skills" / "builtin") in instructions
    assert str(tmp_path / "skills" / "shared" / "scripts") in instructions
    assert str(tmp_path / "profiles") in instructions
    assert "直接使用该实际路径" in instructions
    assert "不是项目根中的必需目录" in instructions
    assert "额外工具权限必须由用户确认" in instructions


def test_missing_shared_agents_document_is_not_silently_ignored(tmp_path):
    source = tmp_path / "agents" / "workspace"
    source.mkdir(parents=True)
    (source / "SOUL.md").write_text("only soul", encoding="utf-8")
    with pytest.raises(HarnessError, match="agents/workspace/AGENTS.md"):
        workspace_documents(tmp_path)


def test_external_request_root_uses_installed_shared_workspace(tmp_path):
    documents = workspace_documents(tmp_path)
    assert all(document.is_file() for document in documents)
    assert {document.name for document in documents} >= {"SOUL.md", "AGENTS.md"}


@pytest.mark.parametrize(("runtime", "adapter_type"), [("codex", CodexAdapter), ("acp", ACPAdapter)])
def test_workspace_changes_reload_instructions_and_resume_original_session(tmp_path, runtime, adapter_type):
    source = workspace_at(tmp_path)
    audit_file = tmp_path / "audit.jsonl"
    request = RunRequest("hello", tmp_path, {
        **os.environ, "EASEL_AGENT_RUNTIME": runtime, "FAKE_HARNESS_MODE": "stable-resume",
        "FAKE_HARNESS_AUDIT": str(audit_file),
    }, command=(sys.executable, str(Path(__file__).parent / "fixtures" / "harness_rpc.py"), runtime),
        timeout=10, session_key="workspace-test")

    async def reject(questions):
        raise AssertionError("No permission expected")

    async def scenario():
        identifiers = []
        current = request
        adapter = adapter_type()
        try:
            for turn in range(3):
                if turn == 2:
                    (source / "SOUL.md").write_text("更新人格", encoding="utf-8")
                await adapter.run(current, lambda *event: None, reject, identifiers.append)
                current = replace(current, native_id=identifiers[-1], prompt="next question")
            messages = [json.loads(line) for line in audit_file.read_text().splitlines()]
            methods = [message.get("method") for message in messages]
            assert methods.count("initialize") == 2
            assert methods.count("thread/start" if runtime == "codex" else "session/new") == 1
            assert methods.count("thread/resume" if runtime == "codex" else "session/load") == 1
            assert len(set(identifiers)) == 1
            assert "更新人格" in json.dumps(messages, ensure_ascii=False)
        finally:
            await close_sessions()

    asyncio.run(scenario())


def test_shared_identity_declares_actual_runtime_config():
    root = Path(__file__).resolve().parents[1]
    identity = (root / "agents" / "workspace" / "AGENTS.md").read_text(encoding="utf-8")
    assert "当前执行引擎的配置管理" in identity
