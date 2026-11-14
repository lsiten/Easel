from __future__ import annotations

import json
from pathlib import Path

import pytest

from easel.analytics_snapshots import (SnapshotError, append_snapshot, load_legacy_history,
                                       load_snapshots, normalize_snapshot)
from easel.ownership import OwnershipError, build_media_manifest, owned_media_path, project_identity


def _project(root: Path, name: str, owner: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / ".easel.json").write_text(json.dumps({"profile": owner}), encoding="utf-8")
    (path / "clip.mp4").write_bytes(b"clip")
    return path


def test_owned_media_requires_manifest_owner(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles" / "demo"
    profiles.mkdir(parents=True)
    monkeypatch.setattr("easel.ownership.PROJECT_ROOT", tmp_path, raising=False)
    project = _project(tmp_path / "outputs", "topic", "demo")
    identity, media = owned_media_path(tmp_path / "outputs", "demo", "topic/clip.mp4")
    assert identity.project == project and media.read_bytes() == b"clip"
    manifest = build_media_manifest(tmp_path / "outputs", "demo", ["topic/clip.mp4"])
    assert manifest[0].size == 4 and len(manifest[0].sha256) == 64
    with pytest.raises(OwnershipError):
        owned_media_path(tmp_path / "outputs", "other", "topic/clip.mp4")


def test_missing_manifest_is_not_owned(tmp_path, monkeypatch):
    profile = tmp_path / "profiles" / "demo"
    profile.mkdir(parents=True)
    monkeypatch.setattr("easel.ownership.PROJECT_ROOT", tmp_path, raising=False)
    project = tmp_path / "outputs" / "topic"
    project.mkdir(parents=True)
    (project / "clip.mp4").write_bytes(b"clip")
    with pytest.raises(OwnershipError):
        project_identity(tmp_path / "outputs", "topic", "demo")


def test_snapshot_is_scoped_and_idempotent(tmp_path):
    payload = {"logged_in": True, "followers": 12, "likes": 4,
               "metrics": {"views": 99}}
    snapshot = normalize_snapshot("demo", "xiaohongshu", payload, observed_at="2026-01-01T00:00:00+00:00")
    assert append_snapshot(tmp_path, snapshot) is True
    assert append_snapshot(tmp_path, snapshot) is False
    loaded = load_snapshots(tmp_path, "demo", "xiaohongshu")
    assert len(loaded) == 1 and dict(loaded[0].account_metrics)["followers"] == 12


def test_legacy_history_is_read_only_compatibility(tmp_path):
    path = tmp_path / "_analytics"
    path.mkdir()
    (path / "xiaohongshu.jsonl").write_text('{"ts": 1, "followers": 2}\n', encoding="utf-8")
    assert load_legacy_history(tmp_path, "xiaohongshu")[0]["followers"] == 2
    with pytest.raises(SnapshotError):
        normalize_snapshot("", "xiaohongshu", {})


def test_snapshot_preserves_account_work_identity_and_list_metrics(tmp_path):
    snapshot = normalize_snapshot('demo', 'xiaohongshu', {
        'account_uid': 'user-1', 'loggedIn': True, 'followers': 0,
        'metrics': [{'label': '曝光数', 'value': '11', 'vs': '+2'}],
        'notes': [{'note_id': 'work-1', 'title': '标题', 'url': 'https://www.xiaohongshu.com/explore/work-1?xsec_token=private',
                   'metrics': {'likes': 0, 'views': None}}],
    }, observed_at='2026-11-14T00:00:00+00:00')
    append_snapshot(tmp_path, snapshot)
    loaded = load_snapshots(tmp_path, 'demo', 'xiaohongshu')[0]
    assert loaded.account_uid == 'user-1'
    assert dict(loaded.content_metrics) == {'曝光数': '11'}
    assert loaded.works[0].platform_work_id == 'work-1'
    assert loaded.works[0].permalink == 'https://www.xiaohongshu.com/explore/work-1'
    assert dict(loaded.works[0].metrics) == {'likes': 0, 'views': None}
    assert loaded.raw_payload_hash and loaded.schema_version == 2


def test_snapshot_rejects_symlink_to_another_personas_store(tmp_path):
    root = tmp_path / '_analytics' / 'snapshots'
    root.mkdir(parents=True)
    (root / 'bob').mkdir()
    (root / 'alice').symlink_to(root / 'bob', target_is_directory=True)
    snapshot = normalize_snapshot('alice', 'xiaohongshu', {'followers': 4})
    with pytest.raises(SnapshotError):
        append_snapshot(tmp_path, snapshot)
