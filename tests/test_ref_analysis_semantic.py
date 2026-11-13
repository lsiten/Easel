import importlib.util
import json
from pathlib import Path


MODULE = Path(__file__).parents[1] / "skills/shared/scripts/ref_analysis.py"
spec = importlib.util.spec_from_file_location("ref_analysis", MODULE)
ref_analysis = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(ref_analysis)


def test_semantic_analysis_preserves_timed_tokens_and_scene_relationships(tmp_path):
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
    assert brief["tokens"][0]["timing_state"] == "known"
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

