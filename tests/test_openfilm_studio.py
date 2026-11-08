"""Project identity and missing-project behavior at the actual Studio HTTP boundary."""
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import web.app as web


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    outputs = tmp_path / "outputs"
    sessions = outputs / "_sessions"
    sessions.mkdir(parents=True)
    monkeypatch.setattr(web, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(web, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(web, "_load_openfilm_mod", lambda: None)
    monkeypatch.setattr(web, "_url_alive", bool)
    monkeypatch.setattr(web, "threading", SimpleNamespace(Thread=lambda **kwargs: SimpleNamespace(start=lambda: None)))
    project = outputs / "current"
    project.mkdir()
    (project / "film.html").write_text("<section>current project</section>", encoding="utf-8")
    web._openfilm_topic_file("chat").write_text("current", encoding="utf-8")
    web._session_openfilm_studio_file("chat").write_text(
        "http://127.0.0.1:9999/open?to=%2Fprojects%2Fold&pin=1", encoding="utf-8")
    web._session_openfilm_film_file("chat").write_text(
        "http://127.0.0.1:9999/p/old-token/film.html", encoding="utf-8")
    local = "http://127.0.0.1:7860"
    client = TestClient(web.app, base_url=local, client=("127.0.0.1", 51234),
                        headers={"Origin": local}, raise_server_exceptions=False)
    yield client, project
    client.close()


def test_deleted_binding_returns_empty_preview_and_cannot_launch(studio):
    client, project = studio
    (project / "film.html").unlink()
    project.rmdir()
    response = client.get("/api/openfilm/studio", params={"session": "chat"})
    assert response.status_code == 200
    result = response.json()
    assert result["has_video"] is False
    assert [result[key] for key in ("studio", "editor", "film", "player", "rev")] == [""] * 5
    launch = client.post("/api/openfilm/studio/launch", json={"session": "chat"})
    assert launch.json() == {"launched": False, "topic": "", "reason": "no-project"}


def test_rebinding_without_fresh_urls_never_reuses_old_live_project(studio):
    client, _ = studio
    result = client.get("/api/openfilm/studio", params={"session": "chat"}).json()
    assert result["topic"] == "current"
    assert result["has_video"] is True
    assert [result[key] for key in ("studio", "editor", "film", "player")] == [""] * 4
    launch = client.post("/api/openfilm/studio/launch", json={"session": "chat"})
    assert launch.json() == {"launched": True, "topic": "current"}


@pytest.mark.parametrize("film_alive,editor_alive", [(True, True), (False, True), (False, False)])
def test_current_project_urls_control_preview_and_launch(studio, monkeypatch, film_alive, editor_alive):
    client, project = studio
    (project / ".film").mkdir()
    (project / ".film/id").write_text("current-id", encoding="utf-8")
    film = "http://127.0.0.1:9999/p/current-id/film.html?film-preview=1"
    editor = "http://127.0.0.1:9999/open?to=%2Fprojects%2Fcurrent-id&pin=1"
    module = SimpleNamespace(
        _film_preview_url=lambda pid: f"http://127.0.0.1:9999/p/{pid}/film.html?film-preview=1",
        _editor_url_fresh=lambda pid: f"http://127.0.0.1:9999/open?to=%2Fprojects%2F{pid}&pin=1",
        capability=lambda: {"available": True}, _check_json=lambda cap: cap,
        _resolve_project=lambda topic: project if topic == "current" else None,
    )
    monkeypatch.setattr(web, "_load_openfilm_mod", lambda: module)
    monkeypatch.setattr(web, "_url_alive", lambda url: (url == film and film_alive) or (url == editor and editor_alive))
    result = client.get("/api/openfilm/studio", params={"session": "chat"}).json()
    assert result["studio"] == (editor if film_alive or editor_alive else "")
    assert result["film"] == (film if film_alive else "")
    launch = client.post("/api/openfilm/studio/launch", json={"session": "chat"}).json()
    assert launch["launched"] is (not (film_alive or editor_alive))
