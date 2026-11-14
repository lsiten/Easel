"""Output directories expose stable project lifecycle metadata for the UI."""
from __future__ import annotations

from web.output_catalog import project_catalog, reference_catalog


def _file(path: str, kind: str) -> dict:
    return {"path": path, "name": path.rsplit("/", 1)[-1], "type": "file", "kind": kind}


def test_declared_video_project_counts_only_real_deliverables():
    node = {
        "type": "dir",
        "children": [
            _file("film/final.mp4", "video"),
            _file("film/cover.png", "image"),
            _file("film/script.md", "text"),
        ],
    }
    meta = {
        "kind": "video",
        "status": "ready",
        "deliverablePaths": ["film/final.mp4", "film/cover.png"],
    }

    catalog = project_catalog(node, meta)

    assert catalog["projectKind"] == "video"
    assert catalog["status"] == "ready"
    assert catalog["deliverableCount"] == 2
    assert catalog["processFileCount"] == 1
    assert catalog["visibleInProjects"] is True


def test_unlabelled_mixed_project_is_kept_and_flagged_for_attention():
    node = {
        "type": "dir",
        "children": [
            _file("draft/clip.mp4", "video"),
            _file("draft/notes.md", "text"),
        ],
    }

    catalog = project_catalog(node, {})

    assert catalog["projectKind"] == "mixed"
    assert catalog["visibleInProjects"] is True
    assert catalog["needsAttention"] is True


def test_empty_project_is_hidden_from_project_gallery():
    catalog = project_catalog({"type": "dir", "children": []}, {})

    assert catalog["visibleInProjects"] is False
    assert catalog["hiddenReason"] == "empty"


def test_reference_and_asset_libraries_never_masquerade_as_projects():
    for library_kind in ("reference", "assets"):
        catalog = reference_catalog(library_kind)
        assert catalog["libraryKind"] == library_kind
        assert catalog["visibleInProjects"] is False
