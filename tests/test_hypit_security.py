import os
from pathlib import Path

import pytest

from easel.hypit_security import (
    LicenseError,
    MediaLimitExceeded,
    ReferenceURLBlocked,
    RetentionItem,
    cleanup_retention,
    license_gate,
    migration_with_rollback,
    redact,
    validate_reference_url,
    validate_video_metadata,
)


def _public_resolver(*args, **kwargs):
    return [(None, None, None, None, ("93.184.216.34", 0))]


def test_reference_hosts_empty_by_default_and_revalidates_private(monkeypatch):
    monkeypatch.delenv("EASEL_VIDEO_REFERENCE_HOSTS", raising=False)
    with pytest.raises(ReferenceURLBlocked):
        validate_reference_url("https://example.com/video", resolver=_public_resolver)
    monkeypatch.setenv("EASEL_VIDEO_REFERENCE_HOSTS", "example.com")
    assert validate_reference_url("https://example.com/video", resolver=_public_resolver).endswith("/video")
    with pytest.raises(ReferenceURLBlocked):
        validate_reference_url("https://user:pass@example.com/video", resolver=_public_resolver)
    with pytest.raises(ReferenceURLBlocked):
        validate_reference_url("https://example.com/video", resolver=lambda *a, **k: [(None, None, None, None, ("127.0.0.1", 0))])


def test_media_caps_redaction_retention_and_rollback(tmp_path):
    with pytest.raises(MediaLimitExceeded):
        validate_video_metadata(byte_size=1_000_000_001, duration_seconds=1, width=1, height=1)
    assert redact({"token": "secret", "path": "/Users/test/a.mp4"})["token"] == "[REDACTED]"
    old = tmp_path / "old.mp4"
    old.write_bytes(b"old")
    assert str(old) in cleanup_retention(tmp_path, now=100, max_age_seconds=1, items=[RetentionItem(str(old), 3, 0)])
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    item = fixture / "state"
    item.write_text("old")
    with pytest.raises(RuntimeError):
        migration_with_rollback(fixture, lambda root: (root / "state").write_text("new") or (_ for _ in ()).throw(RuntimeError()))
    assert item.read_text() == "old"


def test_license_gate_is_local_single_org(monkeypatch):
    monkeypatch.setenv("EASEL_HYPIT_STUDIO_LICENSE", "enabled")
    assert license_gate(attribution="Hypit replication powered by Easel")["scope"] == "local-single-org"
    with pytest.raises(LicenseError):
        license_gate(attribution="Hypit replication powered by Easel", multi_tenant=True)
