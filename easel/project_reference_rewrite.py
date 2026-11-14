"""Rewrite relative media references after physical project moves."""
from __future__ import annotations

import json
import os
import posixpath
import re
from pathlib import Path
from typing import Final, TypeAlias

from easel.project_organization_models import ProjectMove

TEXT_SUFFIXES: Final = {".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".json",
                        ".md", ".txt", ".yaml", ".yml", ".srt", ".vtt"}
_QUOTED_PATH: Final = re.compile(r"""(?P<quote>["'])(?P<path>[^"'\n]{1,300})(?P=quote)""")
_CSS_URL: Final = re.compile(r"url\(\s*(?P<quote>[\"']?)(?P<path>[^)\n]+?)(?P=quote)\s*\)")
_MEDIA_ATTRIBUTE: Final = re.compile(
    r"""(?P<prefix>\b(?:src|href|poster)\s*=\s*)(?P<quote>["'])(?P<path>[^"'\n]{1,300})(?P=quote)"""
)
_MARKDOWN_PATH: Final = re.compile(r"\]\((?P<path>[^)\n]{1,300})\)")
_EXTERNAL: Final = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#|@)")
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


def _relative_reference(path: str, old_owner: str, new_owner: str,
                        moves: dict[str, str], known_sources: set[str]) -> str | None:
    raw, fragment_marker, fragment = path.partition("#")
    raw, query_marker, query = raw.partition("?")
    if not raw or _EXTERNAL.match(raw) or raw.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(old_owner), raw))
    if resolved.startswith("../") or resolved not in known_sources:
        return None
    target = moves.get(resolved, resolved)
    rewritten = posixpath.relpath(target, posixpath.dirname(new_owner) or ".")
    if query_marker:
        rewritten += "?" + query
    if fragment_marker:
        rewritten += "#" + fragment
    return path if rewritten == path else rewritten


def _rewrite_text(text: str, old_owner: str, new_owner: str,
                  moves: dict[str, str], known_sources: set[str], *, css: bool = False) -> str:
    patterns = (_CSS_URL,) if css else (_MEDIA_ATTRIBUTE, _CSS_URL, _MARKDOWN_PATH, _QUOTED_PATH)
    replacements: list[tuple[int, int, str]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            start, end = match.span("path")
            if any(start < existing_end and existing_start < end
                   for existing_start, existing_end, _ in replacements):
                continue
            original = match.group("path")
            value = _relative_reference(
                original.strip(), old_owner, new_owner, moves, known_sources,
            )
            if value is not None and value != original:
                replacements.append((start, end, value))
    for start, end, value in reversed(replacements):
        text = text[:start] + value + text[end:]
    return text


def _rewrite_json_value(value: JsonValue, moves: dict[str, str]) -> JsonValue:
    if isinstance(value, str):
        return moves.get(value, value)
    if isinstance(value, list):
        return [_rewrite_json_value(item, moves) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_json_value(item, moves) for key, item in value.items()}
    return value


def rewrite_references(project: Path, moves: tuple[ProjectMove, ...]) -> int:
    """Rewrite supported relative references and return the changed-file count."""
    mapping = {move.source: move.target for move in moves}
    reverse = {move.target: move.source for move in moves}
    known_sources = set(mapping) | {".easel.json", "film.html"}
    changed = 0
    for path in sorted(project.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(project).parts
        if any(part.startswith(".") for part in parts[:-1]):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(project).as_posix()
        old_owner = reverse.get(relative, relative)
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if path.name == ".easel.json":
            try:
                value = json.loads(original)
            except json.JSONDecodeError:
                continue
            rewritten = json.dumps(_rewrite_json_value(value, mapping), ensure_ascii=False, indent=2) + "\n"
        else:
            rewritten = _rewrite_text(original, old_owner, relative, mapping, known_sources,
                                      css=path.suffix.lower() == ".css")
        if rewritten == original:
            continue
        temporary = path.with_suffix(path.suffix + ".organize.tmp")
        temporary.write_text(rewritten, encoding="utf-8")
        os.replace(temporary, path)
        changed += 1
    return changed
