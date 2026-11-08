"""Music reuse is checked at the shipped CLI before downloading or rendering."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/shared/scripts/openfilm.py"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EASEL_ROOT", str(tmp_path))
    monkeypatch.delenv("EASEL_SESSION_KEY", raising=False)
    catalog = tmp_path / "subprojects/openfilm/packages/openfilm/library"
    catalog.mkdir(parents=True)
    (catalog / "audio.json").write_text(json.dumps({"items": [
        {"id": "audio/music/bed.mp3", "cat": "music"},
        {"id": "audio/music/fresh.mp3", "cat": "music"},
    ]}))
    for name in ("current", "previous"):
        project = tmp_path / "outputs" / name
        (project / "assets").mkdir(parents=True)
        (project / "assets/bed.mp3").write_bytes(b"same music")
        (project / "film.html").write_text('<section><audio src="assets/bed.mp3#t=0,30"></audio></section>')
    runner = tmp_path / "runner"
    runner.write_text(f"#!{sys.executable}\n" + '''
from pathlib import Path
import os
import sys
args = sys.argv[1:]
if args[0] == "render":
    Path("rendered.mp4").write_bytes(b"rendered")
    if os.environ.get("MUSIC_APPEND_ON_RENDER") == "1":
        film = Path("film.html")
        film.write_text(film.read_text() + '<section><audio src="assets/bed.mp3"></audio></section>')
elif "--take" in args:
    source = args[args.index("--take") + 1]
    Path("assets", Path(source).name).write_bytes(b"downloaded music")
''')
    runner.chmod(0o755)
    monkeypatch.setenv("EASEL_OPENFILM", str(runner))
    return tmp_path


def invoke(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=root,
                          capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize("command", [("render",), ("get", "music-check")])
def test_duplicate_music_blocks_render_and_check(workspace: Path, command: tuple[str, ...]):
    result = invoke(workspace, *command, "-p", "current")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "previous" in result.stdout + result.stderr
    assert not (workspace / "outputs/current/rendered.mp4").exists()


@pytest.mark.parametrize("choice", ["audio/music/bed.mp3", "openfilm/audio/music/bed.mp3"])
def test_recent_track_is_blocked_before_take(workspace: Path, choice: str):
    current = workspace / "outputs/current"
    (current / "assets/bed.mp3").unlink()
    (current / "film.html").write_text("<section></section>")
    result = invoke(workspace, "get", "-p", "current", "music-search", "--take", choice)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (current / "assets/bed.mp3").exists()


def test_renaming_music_does_not_bypass_content_check(workspace: Path):
    current = workspace / "outputs/current"
    (current / "assets/bed.mp3").rename(current / "assets/renamed.mp3")
    (current / "film.html").write_text('<audio data-role="music" src="assets/renamed.mp3"></audio>')
    result = invoke(workspace, "render", "-p", "current")
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (current / "rendered.mp4").exists()


@pytest.mark.parametrize("reason,code", [("", 2), ("用户指定的系列主题曲", 0)])
def test_reuse_requires_explicit_reason(workspace: Path, reason: str, code: int):
    result = invoke(workspace, "render", "-p", "current", "--allow-music-reuse", reason)
    assert result.returncode == code, result.stdout + result.stderr
    assert (workspace / "outputs/current/rendered.mp4").exists() == (code == 0)
    if reason:
        assert reason in result.stdout


def test_different_music_is_allowed(workspace: Path):
    current = workspace / "outputs/current"
    (current / "assets/fresh.mp3").write_bytes(b"different music")
    (current / "film.html").write_text('<audio src="assets/fresh.mp3"></audio>')
    result = invoke(workspace, "render", "-p", "current")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workspace / "outputs/current/rendered.mp4").is_file()


@pytest.mark.parametrize("stale", [False, True])
def test_prepared_music_keeps_source_identity_and_receipt_validity(workspace: Path, stale: bool):
    current = workspace / "outputs/current"
    prepared = current / "assets/prepared.mp3"
    prepared.write_bytes(b"same track with a different fade")
    (current / "film.html").write_text('<audio data-role="music" src="assets/prepared.mp3"></audio>')
    (current / "music-selection.json").write_text(json.dumps({
        "file": "assets/prepared.mp3", "source_id": "openfilm/audio/music/bed.mp3",
        "source_sha256": hashlib.sha256(b"same music").hexdigest(),
        "prepared_sha256": hashlib.sha256(prepared.read_bytes()).hexdigest(),
    }))
    if stale:
        prepared.write_bytes(b"changed after checking")
        (workspace / "outputs/previous/film.html").write_text("<section></section>")
    result = invoke(workspace, "render", "-p", "current")
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (current / "rendered.mp4").exists()


def test_unused_asset_does_not_count_as_used_music(workspace: Path):
    (workspace / "outputs/previous/film.html").write_text("<section></section>")
    result = invoke(workspace, "render", "-p", "current")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workspace / "outputs/current/rendered.mp4").is_file()


@pytest.mark.parametrize("markup", [
    '<audio data-role="sfx" src="assets/bed.mp3"></audio>',
    '<audio data-role="voice" src="assets/bed.mp3"></audio>',
    '<audio muted src="assets/bed.mp3"></audio>',
    '<audio volume="0.0" src="assets/bed.mp3"></audio>',
    '<section muted><audio src="assets/bed.mp3"></audio></section>',
    '<section hidden><audio src="assets/bed.mp3"></audio></section>',
])
def test_inactive_music_and_other_audio_roles_do_not_block(workspace: Path, markup: str):
    (workspace / "outputs/current/film.html").write_text(markup)
    result = invoke(workspace, "render", "-p", "current")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workspace / "outputs/current/rendered.mp4").is_file()


def test_missing_current_music_is_not_silently_accepted(workspace: Path):
    (workspace / "outputs/current/assets/bed.mp3").unlink()
    result = invoke(workspace, "get", "-p", "current", "music-check")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "bed.mp3" in result.stdout + result.stderr


def test_duplicate_music_layers_block_even_with_reuse_permission(workspace: Path):
    current = workspace / "outputs/current"
    (current / "film.html").write_text('<section><audio src="assets/bed.mp3#t=0,30"></audio></section>' * 2)
    (workspace / "outputs/previous/film.html").write_text("<section></section>")
    result = invoke(workspace, "render", "-p", "current", "--allow-music-reuse", "系列主题曲")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "叠加" in result.stdout
    assert not (current / "rendered.mp4").exists()


def test_music_merged_during_render_is_not_delivered_as_success(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    (workspace / "outputs/previous/film.html").write_text("<section></section>")
    monkeypatch.setenv("MUSIC_APPEND_ON_RENDER", "1")
    result = invoke(workspace, "render", "-p", "current")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "叠加" in result.stdout


def test_recent_window_excludes_old_projects(workspace: Path):
    old = workspace / "outputs/previous/film.html"
    os.utime(old, (1, 1))
    for index in range(10):
        project = workspace / "outputs" / f"new-{index}"
        project.mkdir()
        film = project / "film.html"
        film.write_text("<section></section>")
        os.utime(film, (100 + index, 100 + index))
    result = invoke(workspace, "render", "-p", "current")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (workspace / "outputs/current/rendered.mp4").is_file()
