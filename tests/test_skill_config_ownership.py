"""Skill API editors must not become a second model-provider configuration source."""

from fastapi.testclient import TestClient

from web import app as web


def test_skill_detail_marks_model_keys_as_channel_owned(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setattr(web, "ENV_FILE", env)
    with TestClient(web.app, base_url="http://localhost:7860",
                    client=("127.0.0.1", 4000)) as client:
        image = client.get("/api/skill/ai-image-gen").json()["apiSpec"]
        image_keys = [
            key
            for provider in image["providers"]
            for key in provider["keys"]
        ]
        assert {key["managedBy"] for key in image_keys} == {"生图"}

        paper = client.get("/api/skill/paper-explainer").json()["apiSpec"]
        paper_keys = {
            key["env"]: key
            for provider in paper["providers"]
            for key in provider["keys"]
        }
        assert paper_keys["MINERU_API_TOKEN"]["managedBy"] == ""
        assert paper_keys["IMG_API_KEY"]["managedBy"] == "生图"

        denied = client.post("/api/env", json={"updates": {"IMG_API_KEY": "second-copy"}})
        assert denied.status_code == 409
        assert denied.json()["detail"] == "model_config_managed_by_channels"
        assert "IMG_API_KEY" not in env.read_text(encoding="utf-8")
