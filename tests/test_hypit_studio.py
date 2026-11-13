import os

import pytest

from easel import hypit_studio


def setup_function():
    hypit_studio._USED.clear()


def test_exact_allowlist_rejects_wildcards_and_paths(monkeypatch):
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_ORIGINS", "https://studio.example, *, https://studio.example/path, http://localhost:4123/")
    assert hypit_studio.allowed_origins() == ("https://studio.example", "http://localhost:4123")


def test_token_claims_are_scoped_short_lived_and_one_shot(monkeypatch):
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_SECRET", "test-secret")
    issued = hypit_studio.issue_token(run_id="run-1", profile_id="profile-1", session_id="session-1", ttl=999, now=100)
    claims = issued["claims"]
    assert claims["aud"] == "hypit-studio"
    assert claims["engine"] == "hypit"
    assert claims["source"] == "easel"
    assert claims["exp"] - claims["iat"] == 300
    assert hypit_studio.validate_token(issued["token"], run_id="run-1", profile_id="profile-1", session_id="session-1", nonce=claims["nonce"], now=101)["jti"] == claims["jti"]
    with pytest.raises(ValueError, match="replayed"):
        hypit_studio.validate_token(issued["token"], run_id="run-1", profile_id="profile-1", session_id="session-1", nonce=claims["nonce"], now=101)


def test_wrong_scope_origin_nonce_and_expiry_are_rejected(monkeypatch):
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_SECRET", "test-secret")
    issued = hypit_studio.issue_token(run_id="run-1", profile_id="profile-1", session_id="session-1", now=100)
    for kwargs, message in [
        ({"run_id": "run-2", "profile_id": "profile-1", "session_id": "session-1"}, "scope"),
        ({"run_id": "run-1", "profile_id": "profile-1", "session_id": "session-1", "nonce": "wrong"}, "nonce"),
        ({"run_id": "run-1", "profile_id": "profile-1", "session_id": "session-1", "now": 401}, "expired"),
    ]:
        with pytest.raises(ValueError, match=message):
            hypit_studio.validate_token(issued["token"], consume=False, **kwargs)


def test_embed_url_contains_only_scoped_transport_claims(monkeypatch):
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_ORIGINS", "https://studio.example")
    url = hypit_studio.embed_url("https://studio.example/editor", "signed", "run-1")
    assert url.startswith("https://studio.example/editor?")
    assert "easel_token=signed" in url and "run_id=run-1" in url
    assert "outputs" not in url and "root" not in url
