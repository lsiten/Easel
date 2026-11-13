from __future__ import annotations

import json
from pathlib import Path
import importlib.util
import sys

import pytest

MODULE = Path(__file__).parents[1] / "skills/shared/scripts/video_run_manifest.py"
spec = importlib.util.spec_from_file_location("video_run_manifest", MODULE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_round_trip_and_session_scope(tmp_path: Path):
    root = mod.resolve_project_root(tmp_path, "profile-a", "session-a", "run-1")
    manifest = mod.VideoRunManifest.create(
        run_id="run-1", attempt_id="attempt-1", build_id="build-1", engine="hypit",
        profile_id="profile-a", session_id="session-a", project_root=root,
        outputs=["outputs/final.mp4"], reports=["reports/report.json"],
        checkpoint={"stage": "analysis"}, provenance={"source": "fixture"},
    )
    path = root / "run-manifest.json"
    manifest.write(path)
    loaded = mod.VideoRunManifest.read(path, project_root=root)
    assert loaded.to_dict() == manifest.to_dict()
    assert loaded.run_id == "run-1" and loaded.session_id == "session-a"


def test_rejects_cross_profile_path_and_unknown_engine(tmp_path: Path):
    root = mod.resolve_project_root(tmp_path, "profile-a", "session-a", "run-1")
    with pytest.raises(mod.ManifestError):
        mod.VideoRunManifest.create(run_id="run-1", attempt_id="attempt-1", build_id="build-1",
                                    engine="unknown", profile_id="profile-a", session_id="session-a",
                                    project_root=root)
    with pytest.raises(mod.ManifestError):
        mod.VideoRunManifest.create(run_id="run-1", attempt_id="attempt-1", build_id="build-1",
                                    engine="hypit", profile_id="profile-a", session_id="session-a",
                                    project_root=root, outputs=["../../profile-b/secret"])


def test_legacy_openfilm_pointer_is_scoped(tmp_path: Path):
    project = tmp_path / "outputs" / "legacy"
    project.mkdir(parents=True)
    (project / "film.html").write_text("<html>")
    pointer = tmp_path / "outputs" / "_sessions"
    pointer.mkdir(parents=True)
    (pointer / "chat-a.openfilm-topic.txt").write_text("legacy")
    assert mod.read_openfilm_session_pointer(tmp_path, "chat-a") == project
    (pointer / "chat-a.openfilm-topic.txt").write_text("../outside")
    assert mod.read_openfilm_session_pointer(tmp_path, "chat-a") is None


def test_atomic_write_rejects_runtime_paths_and_invalid_transition(tmp_path: Path):
    root = mod.resolve_project_root(tmp_path, "profile-a", "session-a", "run-1")
    with pytest.raises(mod.ManifestError):
        mod.VideoRunManifest.create(run_id="run-1", attempt_id="attempt-1", build_id="build-1",
                                    engine="hypit", profile_id="profile-a", session_id="session-a",
                                    project_root=root, outputs=[".hypit/project.svml"])
    run = mod.VideoRunManifest.create(run_id="run-1", attempt_id="attempt-1", build_id="build-1",
                                      engine="openfilm", profile_id="profile-a", session_id="session-a",
                                      project_root=root)
    assert run.transition("running").state == "running"
    with pytest.raises(mod.ManifestError):
        run.transition("succeeded")
