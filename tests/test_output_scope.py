"""Local project visibility follows manifest ownership at the HTTP boundary."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import web.app as web


@pytest.mark.parametrize("persona,expected", [
    ("账号甲", {"owned-a"}), ("账号乙", {"owned-b"}),
    ("missing", set()), ("", {"owned-a", "owned-b", "legacy", "broken"}),
])
def test_output_library_filters_local_projects_by_owner(tmp_path, monkeypatch, persona, expected):
    for topic, owner in [("owned-a", "账号甲"), ("owned-b", "账号乙"), ("legacy", None), ("broken", None)]:
        project = tmp_path / topic
        project.mkdir()
        (project / "film.mp4").write_bytes(b"video")
        if owner:
            (project / ".easel.json").write_text(json.dumps({"profile": owner}), encoding="utf-8")
    (tmp_path / "broken/.easel.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(web, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(web, "_content_sources", lambda: {})
    monkeypatch.setattr(web, "_assets_mod", lambda: SimpleNamespace(tree_for_library=lambda _: None))
    with TestClient(web.app, base_url="http://127.0.0.1:7860") as client:
        response = client.get("/api/outputs", params={"persona": persona})
    assert response.status_code == 200
    assert {project["name"] for project in response.json()} == expected
