import hashlib
from pathlib import Path
import pytest
from easel.video_artifact_boundary import ArtifactBoundaryError, ArtifactRecord, export_artifacts, import_artifacts, import_native_sources


def test_hypit_round_trip_keeps_native_namespace_and_exports_derived(tmp_path):
    src = tmp_path / "hypit"; src.mkdir()
    (src / "project").mkdir(); (src / "source").mkdir(); (src / "run").mkdir()
    (src / "project" / "project.json").write_text("native", encoding="utf-8")
    video = src / "result.mp4"; video.write_bytes(b"mp4")
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    records = import_artifacts(source_root=src, destination_root=tmp_path / "store", source_engine="hypit", target_profile="demo", manifest={"artifacts":[{"kind":"mp4","path":"result.mp4","checksum":digest,"attribution":"synthetic"}]})
    native = import_native_sources(source_root=src, destination_root=tmp_path / "store", source_engine="hypit", target_profile="demo", sources={"project":"project","source":"source","run":"run"})
    assert records[0].source_engine == "hypit"
    assert native["project"].startswith("engines/hypit/demo/native")
    assert (tmp_path / "store" / records[0].path).is_file()
    exported = export_artifacts(source_root=tmp_path / "store", destination_root=tmp_path / "export", source_engine="hypit", target_profile="demo", records=records)
    assert exported == records
    assert (tmp_path / "export" / records[0].path).is_file()
    assert not list((tmp_path / "store" / "engines" / "hypit" / "demo" / "native").glob(".native-*"))

@pytest.mark.parametrize("manifest", [
    {"artifacts":[{"kind":"mp4","path":"../x.mp4","checksum":"0"*64,"attribution":"x"}]},
    {"artifacts":[{"kind":"manifest","path":"film.html","checksum":"0"*64,"attribution":"x"}]},
])
def test_rejects_path_escape_and_implicit_film(tmp_path, manifest):
    with pytest.raises(ArtifactBoundaryError): import_artifacts(source_root=tmp_path, destination_root=tmp_path / "out", source_engine="hypit", target_profile="demo", manifest=manifest)

def test_rejects_checksum_and_overwrite(tmp_path):
    src = tmp_path / "src"; src.mkdir(); (src / "x.mp4").write_bytes(b"ok")
    with pytest.raises(ArtifactBoundaryError): import_artifacts(source_root=src, destination_root=tmp_path / "out", source_engine="hypit", target_profile="demo", manifest={"artifacts":[{"kind":"mp4","path":"x.mp4","checksum":"0"*64,"attribution":"x"}]})


def test_duplicate_destination_is_rejected_before_commit(tmp_path):
    src = tmp_path / "src"; (src / "a").mkdir(parents=True); (src / "b").mkdir(); (src / "a" / "result.mp4").write_bytes(b"a"); (src / "b" / "result.mp4").write_bytes(b"b")
    digest_a = hashlib.sha256(b"a").hexdigest(); digest_b = hashlib.sha256(b"b").hexdigest()
    manifest = {"artifacts": [{"kind":"mp4","path":"a/result.mp4","checksum":digest_a,"attribution":"x"}, {"kind":"mp4","path":"b/result.mp4","checksum":digest_b,"attribution":"x"}]}
    with pytest.raises(ArtifactBoundaryError, match="duplicate artifact destination"):
        import_artifacts(source_root=src, destination_root=tmp_path / "out", source_engine="hypit", target_profile="demo", manifest=manifest)
    assert not [path for path in (tmp_path / "out").rglob("*") if path.is_file()]

def test_export_partial_failure_cleans_staged_outputs(tmp_path):
    src = tmp_path / "src"; src.mkdir(); (src / "one.mp4").write_bytes(b"one"); (src / "two.mp4").write_bytes(b"two")
    good = ArtifactRecord("mp4", "one.mp4", hashlib.sha256(b"one").hexdigest(), "hypit", "demo", "x")
    bad = ArtifactRecord("mp4", "two.mp4", "0" * 64, "hypit", "demo", "x")
    with pytest.raises(ArtifactBoundaryError, match="checksum mismatch"):
        export_artifacts(source_root=src, destination_root=tmp_path / "out", source_engine="hypit", target_profile="demo", records=[good, bad])
    assert not [path for path in (tmp_path / "out").rglob("*") if path.is_file()]
