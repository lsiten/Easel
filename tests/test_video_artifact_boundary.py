import hashlib
from pathlib import Path
import pytest
from easel.video_artifact_boundary import ArtifactBoundaryError, import_artifacts, import_native_sources


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

@pytest.mark.parametrize("manifest", [
    {"artifacts":[{"kind":"mp4","path":"../x.mp4","checksum":"0"*64,"attribution":"x"}]},
    {"artifacts":[{"kind":"manifest","path":"film.html","checksum":"0"*64,"attribution":"x"}]},
])
def test_rejects_path_escape_and_implicit_film(tmp_path, manifest):
    with pytest.raises(ArtifactBoundaryError): import_artifacts(source_root=tmp_path, destination_root=tmp_path / "out", source_engine="hypit", target_profile="demo", manifest=manifest)

def test_rejects_checksum_and_overwrite(tmp_path):
    src = tmp_path / "src"; src.mkdir(); (src / "x.mp4").write_bytes(b"ok")
    with pytest.raises(ArtifactBoundaryError): import_artifacts(source_root=src, destination_root=tmp_path / "out", source_engine="hypit", target_profile="demo", manifest={"artifacts":[{"kind":"mp4","path":"x.mp4","checksum":"0"*64,"attribution":"x"}]})
