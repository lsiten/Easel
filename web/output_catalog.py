"""Classify output directories into stable project, video, and reference views."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

ProjectKind = Literal["video", "image", "article", "mixed", "unknown"]
LibraryKind = Literal["project", "reference", "assets"]


class ProjectCatalog(TypedDict):
    libraryKind: LibraryKind
    visibleInProjects: bool
    projectKind: ProjectKind
    projectKindLabel: str
    status: str
    deliverableCount: int
    processFileCount: int
    mediaCount: int
    videoCount: int
    imageCount: int
    textCount: int
    needsAttention: bool
    hiddenReason: str


_KIND_LABELS: dict[ProjectKind, str] = {
    "video": "视频项目",
    "image": "图片项目",
    "article": "图文项目",
    "mixed": "综合项目",
    "unknown": "待分类",
}


def _walk_files(node: dict) -> list[dict]:
    if node.get("type") == "file":
        return [node]
    files: list[dict] = []
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                files.extend(_walk_files(child))
    return files


def _infer_kind(files: list[dict], meta: dict) -> ProjectKind:
    declared = str(meta.get("kind") or "")
    if declared == "video":
        return "video"
    if declared in {"article", "xhs-note", "cards", "poster"}:
        return "article"
    video_count = sum(1 for item in files if item.get("kind") == "video")
    image_count = sum(1 for item in files if item.get("kind") == "image")
    text_count = sum(1 for item in files if item.get("kind") == "text")
    has_film = any(Path(str(item.get("path", ""))).name == "film.html" for item in files)
    if video_count or has_film:
        return "video" if not text_count else "mixed"
    if image_count and not text_count:
        return "image"
    if text_count and not image_count:
        return "article"
    if image_count or text_count:
        return "mixed"
    return "unknown"


def project_catalog(node: dict, meta: dict) -> ProjectCatalog:
    """Build a read-only display catalog without moving any project files."""
    files = _walk_files(node)
    deliverable_paths = {
        str(path)
        for path in (meta.get("deliverablePaths") or [])
        if isinstance(path, str) and path
    }
    video_count = sum(1 for item in files if item.get("kind") == "video")
    image_count = sum(1 for item in files if item.get("kind") == "image")
    text_count = sum(1 for item in files if item.get("kind") == "text")
    kind = _infer_kind(files, meta)
    status = str(meta.get("status") or ("draft" if files else "empty"))
    deliverable_count = sum(1 for item in files if str(item.get("path", "")) in deliverable_paths)
    if not meta and kind != "unknown":
        deliverable_count = video_count if kind == "video" else max(image_count, text_count)
    process_file_count = max(0, len(files) - deliverable_count)
    hidden_reason = "empty" if not files else ""
    return {
        "libraryKind": "project",
        "visibleInProjects": bool(files),
        "projectKind": kind,
        "projectKindLabel": _KIND_LABELS[kind],
        "status": status,
        "deliverableCount": deliverable_count,
        "processFileCount": process_file_count,
        "mediaCount": video_count + image_count,
        "videoCount": video_count,
        "imageCount": image_count,
        "textCount": text_count,
        "needsAttention": not bool(meta) or kind == "unknown",
        "hiddenReason": hidden_reason,
    }


def reference_catalog(library_kind: Literal["reference", "assets"]) -> ProjectCatalog:
    """Catalog nodes that are sources or the persona asset index, not projects."""
    label = "参考资料源" if library_kind == "reference" else "素材库"
    return {
        "libraryKind": library_kind,
        "visibleInProjects": False,
        "projectKind": "unknown",
        "projectKindLabel": label,
        "status": "reference",
        "deliverableCount": 0,
        "processFileCount": 0,
        "mediaCount": 0,
        "videoCount": 0,
        "imageCount": 0,
        "textCount": 0,
        "needsAttention": False,
        "hiddenReason": library_kind,
    }
