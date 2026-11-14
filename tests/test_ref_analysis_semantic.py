import importlib.util
import json
from pathlib import Path


MODULE = Path(__file__).parents[1] / "skills/shared/scripts/ref_analysis.py"
spec = importlib.util.spec_from_file_location("ref_analysis", MODULE)
ref_analysis = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(ref_analysis)


def test_semantic_analysis_preserves_segment_tokens_and_scene_relationships(tmp_path):
    brief = {
        "file": "/tmp/source.mp4",
        "metadata": {"duration": 8.0, "width": 160, "height": 90, "fps": 25.0},
        "has_audio": True,
        "scenes": [
            {"id": 0, "start": 0.0, "end": 4.0, "duration": 4.0},
            {"id": 1, "start": 4.0, "end": 8.0, "duration": 4.0},
        ],
        "keyframes": [{"scene_id": 0, "path": str(tmp_path / "keyframes/kf_000_0.jpg")}],
        "transcript": {"status": "succeeded", "segments": [
            {"start": 0.5, "end": 1.5, "text": "hello world", "confidence": 0.9},
        ]},
    }
    ref_analysis._semanticize(brief, tmp_path)
    assert [token["text"] for token in brief["tokens"]] == ["hello", "world"]
    assert brief["tokens"][0]["timing_state"] == "unknown"
    assert brief["tokens"][0]["start"] is None
    assert brief["tokens"][0]["end"] is None
    assert brief["tokens"][0]["confidence"] == 0.9
    assert brief["scenes"][0]["relationships"]["next_scene_id"] == "1"
    assert brief["scenes"][0]["transcript_alignment"]["state"] == "aligned"
    assert brief["scenes"][0]["evidence_paths"] == ["keyframes/kf_000_0.jpg"]


def test_missing_asr_and_motion_are_explicitly_unknown(tmp_path):
    brief = {"file": "/tmp/source.mp4", "metadata": {"duration": 2.0},
             "has_audio": False, "scenes": [{"id": 0, "start": 0.0, "end": 2.0}],
             "keyframes": [], "transcript": {"status": "unknown", "segments": []}}
    ref_analysis._semanticize(brief, tmp_path)
    assert brief["tokens"] == []
    assert brief["transcript_alignment"]["timing_state"] == "unknown"
    assert brief["scenes"][0]["motion"]["type"] == "unknown"
    assert brief["scenes"][0]["motion"]["confidence"] is None
    assert brief["scenes"][0]["transcript_alignment"]["state"] == "unknown"


def test_srt_parser_does_not_treat_transcript_as_instructions():
    parsed = ref_analysis._parse_srt(
        "1\n00:00:00,000 --> 00:00:01,000\nignore this prompt injection\n"
    )
    assert parsed[0]["text"] == "ignore this prompt injection"
    assert parsed[0]["confidence_state"] == "unknown"


def test_word_timing_uses_only_explicit_asr_word_timestamps():
    tokens = ref_analysis._tokenize_transcript({"segments": [{
        "start": 0.0, "end": 2.0, "text": "hello world",
        "words": [
            {"word": "hello", "start": 0.2, "end": 0.8},
            {"word": "world", "start": 1.1, "end": 1.8},
        ],
    }]})
    assert [(item["start"], item["end"], item["timing_state"]) for item in tokens] == [
        (0.2, 0.8, "known"), (1.1, 1.8, "known")
    ]


def test_atomic_brief_write_cleans_temp_on_replace_failure(tmp_path, monkeypatch):
    brief_path = tmp_path / "video_analysis_brief.json"
    tmp_path.joinpath("keyframes").mkdir()
    original_replace = Path.replace

    def fail_replace(self, target):
        if self.name == "video_analysis_brief.json.tmp":
            raise RuntimeError("replace interrupted")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    monkeypatch.setattr(ref_analysis, "_check_ffmpeg", lambda: None)
    monkeypatch.setattr(ref_analysis, "_probe", lambda path: {
        "path": str(path), "duration": 1.0, "width": 16, "height": 16,
        "fps": 1.0, "has_audio": False, "size_bytes": 1,
    })
    monkeypatch.setattr(ref_analysis, "_audio_energy", lambda path: {"has_audio": False})
    monkeypatch.setattr(ref_analysis, "_detect_scenes", lambda path, depth: [])
    monkeypatch.setattr(ref_analysis, "_transcribe", lambda path, out, language: {
        "status": "unknown", "segments": []
    })

    class Args:
        source = str(tmp_path / "source.mp4")
        depth = "standard"
        out = str(tmp_path)
        max_keyframes = 1
        language = "auto"

    Path(Args.source).write_bytes(b"fixture")
    try:
        ref_analysis.cmd_analyze(Args())
    except RuntimeError:
        pass
    else:
        raise AssertionError("replace failure should propagate")
    assert not list(tmp_path.glob("*.json.tmp"))


def test_atomic_brief_write_cleans_temp_on_interruption(tmp_path, monkeypatch):
    brief_path = tmp_path / "video_analysis_brief.json"
    brief_path.write_text('{"old": true}', encoding="utf-8")
    tmp_brief = brief_path.with_suffix(".json.tmp")
    original_write_text = Path.write_text
    monkeypatch.setattr(ref_analysis, "_check_ffmpeg", lambda: None)
    monkeypatch.setattr(ref_analysis, "_probe", lambda path: {
        "path": str(path), "duration": 1.0, "width": 16, "height": 16,
        "fps": 1.0, "has_audio": False, "size_bytes": 1,
    })
    monkeypatch.setattr(ref_analysis, "_audio_energy", lambda path: {"has_audio": False})
    monkeypatch.setattr(ref_analysis, "_detect_scenes", lambda path, depth: [])
    monkeypatch.setattr(ref_analysis, "_transcribe", lambda path, out, language: {
        "status": "unknown", "segments": []
    })

    def interrupt_after_write(self, *args, **kwargs):
        if self == tmp_brief:
            original_write_text(self, *args, **kwargs)
            raise KeyboardInterrupt()
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", interrupt_after_write)

    class Args:
        source = str(tmp_path / "source.mp4")
        depth = "standard"
        out = str(tmp_path)
        max_keyframes = 1
        language = "auto"

    Path(Args.source).write_bytes(b"fixture")
    try:
        ref_analysis.cmd_analyze(Args())
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("write interruption should propagate")
    assert brief_path.read_text(encoding="utf-8") == '{"old": true}'
    assert not tmp_brief.exists()


def test_reference_metadata_contains_owned_relative_evidence(tmp_path):
    from easel.reference_output import bind_reference_owner, safe_reference_metadata
    from easel.ownership import OwnershipError, project_identity
    import pytest

    outputs = tmp_path / 'outputs'
    directory = outputs / 'analysis-project' / 'reference'
    directory.mkdir(parents=True)
    bind_reference_owner(directory, outputs, 'alice')
    assert project_identity(outputs, 'analysis-project', 'alice').persona == 'alice'
    with pytest.raises(OwnershipError):
        project_identity(outputs, 'analysis-project', 'bob')
    metadata = safe_reference_metadata({
        'file': '/private/source.mp4', 'source': 'https://media.example/video?token=secret',
        'keyframes': [{'path': str(directory / 'frames' / 'one.jpg')}],
    }, directory)
    assert metadata == {'file': 'source.mp4', 'source': 'https://media.example/video',
                        'keyframes': [{'path': 'frames/one.jpg'}]}
