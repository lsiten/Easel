import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

from web import app as web
from easel.publish_ledger import RunState


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path)
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "ENV_FILE", env)
    monkeypatch.setattr(web, '_publish_account_uid', lambda platform, profile: 'verified-test-account')
    async def verified_actor(platform, profile, expected_uid):
        return None
    monkeypatch.setattr(web, '_verify_publish_actor', verified_actor)
    return TestClient(web.app, base_url="http://localhost:7860", headers={"Origin": "http://localhost:7860"})


def test_capability_projection_is_read_only(client):
    response = client.get("/api/platform-capabilities")
    assert response.status_code == 200
    assert response.json()["xiaohongshu"]["mediaConstraints"]["requiresMedia"] is True


def test_precheck_returns_digest_without_side_effect(client):
    response = client.post("/api/publish/zhihu/precheck", json={"title": "标题", "body": "正文"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["persona"] == "_general"
    assert len(payload["approvalDigest"]) == 64
    assert not (web.OUTPUTS_DIR / "_publish" / "ledger.sqlite3").exists()


def test_precheck_requires_persona_for_media(client, tmp_path):
    project = tmp_path / "topic"
    project.mkdir()
    (project / "cover.png").write_bytes(b"image")
    response = client.post("/api/publish/xiaohongshu/precheck", json={
        "title": "标题", "body": "正文", "media": ["topic/cover.png"],
    })
    assert response.status_code == 400


def test_legacy_export_can_be_closed_after_migration(client, tmp_path, monkeypatch):
    project = tmp_path / "topic"
    project.mkdir()
    (project / "cover.png").write_bytes(b"image")
    monkeypatch.setenv("EASEL_ALLOW_LEGACY_UNSCOPED_EXPORT", "0")
    response = client.post("/api/manual-publish/xiaohongshu/export", json={
        "title": "标题", "body": "正文", "media": ["topic/cover.png"], "reviewed": True,
    })
    assert response.status_code == 400


def test_same_publish_intent_is_not_reexecuted(client, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    payload = {"title": "标题", "body": "正文", "run_id": "stable-run"}
    payload['reviewed'] = True
    payload['approval_digest'] = client.post('/api/publish/zhihu/precheck', json=payload).json()['approvalDigest']
    first = client.post("/api/publish/zhihu", json=payload)
    second = client.post("/api/publish/zhihu", json=payload)
    assert first.status_code == 200 and second.status_code == 200
    assert sum("web_publisher.py" in str(command) for command in calls) == 1
    assert second.json()["message"] == "已存在相同发布请求，未重复提交"


@pytest.mark.parametrize("run_id", ["../../escape", "/tmp/escape", "bad\\path"])
def test_publish_run_id_cannot_escape_status_or_sms_scope(client, run_id):
    status = client.get("/api/publish/douyin/status", params={"run_id": run_id})
    assert status.status_code == 400
    sms = client.post("/api/publish/douyin/sms", json={"code": "123456", "run_id": run_id})
    assert sms.status_code == 400


def test_reconcile_endpoint_does_not_trust_client_verified_claim(client, monkeypatch):
    async def readback(platform, persona=''):
        return {'platform': platform, 'loggedIn': True, 'account_uid': 'real-account', 'notes': []}
    monkeypatch.setattr(web, '_fetch_analytics', readback)
    ledger = web._publish_ledger()
    run = ledger.create_run(
        platform="zhihu", persona="禾城AI造物局", idempotency_key="reconcile-test",
        approval_digest="a", media_manifest_hash="m", account_id=web._pk("禾城AI造物局", "zhihu"),
    )
    ledger.transition(run.run_id, expected=RunState.PREPARED, target=RunState.APPROVED, actor="api")
    ledger.transition(run.run_id, expected=RunState.APPROVED, target=RunState.QUEUED, actor="api")
    ledger.transition(run.run_id, expected=RunState.QUEUED, target=RunState.RUNNING, actor="api")
    ledger.transition(run.run_id, expected=RunState.RUNNING, target=RunState.UNKNOWN, actor="publisher")
    response = client.post("/api/publish/zhihu/reconcile", json={
        "run_id": run.run_id, "persona": "禾城AI造物局", "outcome": "verified",
        "account_uid": web._pk("禾城AI造物局", "zhihu"), "platform_work_id": "work-1",
    })
    assert response.status_code == 200
    assert response.json()["state"] == "unknown"
    assert response.json()['outcome'] == 'unverified'
