import json

from fastapi.testclient import TestClient

from web import app as web


def test_persona_schedule_uses_scoped_store(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path)
    client = TestClient(web.app, base_url="http://localhost:7860",
                        headers={"Origin": "http://localhost:7860"})
    payload = {"title": "画像草稿", "date": "2026-11-14", "status": "draft"}
    response = client.post("/api/schedule?persona=%E7%A6%BE%E5%9F%8EAI%E9%80%A0%E7%89%A9%E5%B1%80", json=payload)
    assert response.status_code == 200, response.text
    assert not (tmp_path / "_schedule.json").exists()
    scoped = tmp_path / "_schedule" / "禾城AI造物局.json"
    assert scoped.is_file()
    assert json.loads(scoped.read_text(encoding="utf-8"))[0]["title"] == "画像草稿"
    assert client.get("/api/schedule?persona=%E7%A6%BE%E5%9F%8EAI%E9%80%A0%E7%89%A9%E5%B1%80").json()[0]["title"] == "画像草稿"
    assert client.get("/api/schedule").json() == []
