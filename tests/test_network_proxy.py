from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easel.network_proxy import (
    ProxyConfig,
    load_proxy_config,
    proxy_environment,
    save_proxy_config,
)
from web import network_settings_router


@pytest.fixture(autouse=True)
def restore_environment():
    original = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(original)


def _client(env_file: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(network_settings_router, "ENV_FILE", env_file)
    app = FastAPI()
    app.include_router(network_settings_router.router)
    return TestClient(app)


def test_custom_proxy_overrides_inherited_socks_for_subprocesses(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    save_proxy_config(env_file, ProxyConfig(
        mode="custom",
        http_proxy="http://127.0.0.1:7890",
        https_proxy="http://127.0.0.1:7890",
        all_proxy="",
        no_proxy="localhost,127.0.0.1",
    ))
    inherited = {
        "HTTP_PROXY": "socks5h://127.0.0.1:7890",
        "HTTPS_PROXY": "socks5h://127.0.0.1:7890",
    }

    resolved = proxy_environment(inherited, load_proxy_config(env_file))

    assert resolved["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert resolved["http_proxy"] == "http://127.0.0.1:7890"
    assert resolved["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert "ALL_PROXY" not in resolved


def test_direct_mode_removes_all_proxy_variables() -> None:
    inherited = {
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "https_proxy": "http://127.0.0.1:7890",
        "ALL_PROXY": "socks5h://127.0.0.1:7890",
        "NO_PROXY": "localhost",
    }

    resolved = proxy_environment(inherited, ProxyConfig(mode="direct"))

    assert not any(key.upper().endswith("_PROXY") and key in resolved for key in inherited)


def test_saved_proxy_values_take_precedence_over_process_environment(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    save_proxy_config(env_file, ProxyConfig(
        mode="custom",
        http_proxy="http://saved.example:8080",
    ))
    monkeypatch.setenv("HTTP_PROXY", "http://inherited.example:9090")

    config = load_proxy_config(env_file)

    assert config.http_proxy == "http://saved.example:8080"


def test_proxy_api_saves_and_masks_credentials(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    client = _client(env_file, monkeypatch)

    response = client.post("/api/settings/proxy", json={
        "mode": "custom",
        "updates": {
            "HTTP_PROXY": "http://alice:secret@127.0.0.1:7890",
            "HTTPS_PROXY": "http://alice:secret@127.0.0.1:7890",
            "NO_PROXY": "localhost,127.0.0.1",
        },
    })

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "custom"
    assert payload["httpProxy"]["value"] == "http://alice:***@127.0.0.1:7890"
    assert "secret" not in response.text
    assert os.environ["HTTP_PROXY"] == "http://alice:secret@127.0.0.1:7890"
    assert "HTTP_PROXY=http://alice:secret@127.0.0.1:7890" in env_file.read_text(encoding="utf-8")


def test_proxy_api_direct_mode_clears_saved_and_runtime_proxy(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    save_proxy_config(env_file, ProxyConfig(
        mode="custom", http_proxy="http://127.0.0.1:7890"))
    client = _client(env_file, monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "socks5h://127.0.0.1:7890")

    response = client.post("/api/settings/proxy", json={"mode": "direct", "updates": {}})

    assert response.status_code == 200, response.text
    assert "HTTP_PROXY" not in os.environ
    assert "HTTP_PROXY=" not in env_file.read_text(encoding="utf-8")
