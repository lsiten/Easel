"""Registering an asset source indexes it without moving files before the user asks."""
from __future__ import annotations

from types import SimpleNamespace

import web.app as web


def test_registering_asset_source_does_not_physically_organize_before_explicit_ai_request(monkeypatch):
    calls: list[tuple[str, str]] = []

    class Library:
        def add(self, profile: str, directories: list[str]):
            calls.append(("add", profile))
            return {"added": directories, "already": [], "invalid": [], "sources": directories}

        def organize(self, profile: str):
            calls.append(("organize", profile))
            return {"placed": 3, "skipped": ["missing.png"]}

    monkeypatch.setattr(web, "_assets_mod", lambda: Library())

    result = web.api_assets_add(web.AssetsAddRequest(directory="/tmp/source"), persona="甲")

    assert calls == [("add", "甲")]
    assert "auto_organize" not in result
