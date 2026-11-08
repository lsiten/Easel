"""Recent music inventory and reuse checks for project-bound OpenFilm commands."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import os
from pathlib import Path
import re
from typing import Final
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from artifact_checkpoints import file_digest

RECENT_PROJECTS: Final = 10


class CatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    cat: str


class Catalog(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: tuple[CatalogItem, ...]


class MusicSelection(BaseModel):
    model_config = ConfigDict(frozen=True)
    file: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    prepared_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MusicMetadataError(RuntimeError):
    """The selection receipt no longer describes the music used in the film."""


@dataclass(frozen=True, slots=True)
class MusicUse:
    project: Path
    source: str
    digest: str = ""
    identity: str = ""
    at: float = 0.0


@dataclass(frozen=True, slots=True)
class MusicRequest:
    root: Path
    project: Path
    command: str
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MusicResult:
    arguments: tuple[str, ...]
    lines: tuple[str, ...] = ()
    handled: bool = False
    code: int = 0


class MusicTags(HTMLParser):
    """Read music references; narration and sound effects have independent reuse rules."""

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self.names = names
        self.sources: list[tuple[str, float]] = []
        self.suppressed_tracks: list[bool] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "section":
            self.suppressed_tracks.append(any(self.suppressed_tracks)
                                          or "muted" in attributes or "hidden" in attributes)
            return
        if tag != "audio":
            return
        source = attributes.get("src") or ""
        role = attributes.get("data-role") or ""
        if (role in {"sfx", "voice", "narration"} or "muted" in attributes
                or any(self.suppressed_tracks)
                or re.fullmatch(r"[+]?(?:0+(?:\.0*)?|\.0+)", (attributes.get("volume") or "1").strip())):
            return
        path = unquote(urlsplit(source).path)
        if (role == "music" or Path(path).name in self.names
                or re.search(r"(?:^|[/_-])(?:bgm|music|bed)(?:[/_.-]|$)", path, re.I)):
            self.sources.append((source, float(attributes.get("at") or "0")))

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self.suppressed_tracks:
            self.suppressed_tracks.pop()


def library_directory(root: Path) -> Path:
    override = os.environ.get("OPENFILM_BUILT_IN_LIBRARY")
    return Path(override) if override else root / "subprojects/openfilm/packages/openfilm/library"


def music_names(root: Path) -> frozenset[str]:
    catalog = library_directory(root) / "audio.json"
    if not catalog.is_file():
        return frozenset()
    parsed = Catalog.model_validate_json(catalog.read_bytes())
    return frozenset(Path(item.id).name for item in parsed.items if item.cat == "music")


def music_uses(project: Path, names: frozenset[str]) -> tuple[MusicUse, ...]:
    receipt = project / "music-selection.json"
    selection = MusicSelection.model_validate_json(receipt.read_bytes()) if receipt.is_file() else None
    if selection:
        names = names | {Path(selection.file).name}
    tags = MusicTags(names)
    tags.feed((project / "film.html").read_text(encoding="utf-8"))
    uses: list[MusicUse] = []
    for source, at in tags.sources:
        parsed = urlsplit(source)
        if parsed.scheme in {"http", "https", "data", "blob"} or parsed.netloc:
            uses.append(MusicUse(project, source.split("#", 1)[0], at=at))
        else:
            path = Path(unquote(parsed.path))
            path = path if path.is_absolute() else project / path
            digest = file_digest(path)
            identity = path.name if path.name in names else ""
            if selection and path.resolve() == (project / selection.file).resolve():
                if digest != selection.prepared_sha256:
                    raise MusicMetadataError(f"{receipt}: 音频已改变，请重新核验并更新选曲记录。")
                digest = selection.source_sha256
                identity = Path(selection.source_id.removeprefix("openfilm/")).name
            uses.append(MusicUse(project, source, digest, identity, at))
    return tuple(uses)


def recent_music(request: MusicRequest, names: frozenset[str]) -> tuple[MusicUse, ...]:
    films = [film for film in (request.root / "outputs").glob("*/film.html")
             if not film.parent.name.startswith(("_", "."))
             and film.parent.resolve() != request.project.resolve()]
    films.sort(key=lambda film: (film.stat().st_mtime_ns, str(film)), reverse=True)
    return tuple(use for film in films[:RECENT_PROJECTS] for use in music_uses(film.parent, names))


def option(arguments: tuple[str, ...], name: str) -> str:
    for index, value in enumerate(arguments):
        if value.startswith(name + "="):
            return value[len(name) + 1:]
        if value == name and index + 1 < len(arguments):
            return arguments[index + 1]
    return ""


def prepare_music(request: MusicRequest) -> MusicResult:
    """Block recent repeats before side effects; report history with free-library searches."""
    args = request.arguments
    relevant = request.command == "render" or (request.command == "get" and args
                  and args[0] in {"music-search", "music-check", "music-history"})
    if not relevant:
        return MusicResult(args)
    reason = option(args, "--allow-music-reuse").strip()
    forwarded = list(args)
    for index, value in reversed(list(enumerate(args))):
        if value.startswith("--allow-music-reuse="):
            del forwarded[index]
        elif value == "--allow-music-reuse":
            del forwarded[index:index + 2]
    if any(value.startswith("--allow-music-reuse") for value in args) and (not reason or reason.startswith("--")):
        return MusicResult(tuple(forwarded), ("BLOCK 配乐复用必须填写原因：--allow-music-reuse '用户指定系列主题曲'",), True, 2)
    try:
        names = music_names(request.root)
        history = recent_music(request, names)
        taking = option(args, "--take")
        checking = request.command == "render" or (args and args[0] == "music-check")
        uses = music_uses(request.project, names) if checking else ()
    except (OSError, UnicodeError, ValidationError, ValueError, MusicMetadataError) as error:
        return MusicResult(tuple(forwarded), (f"BLOCK 无法核验配乐：{error}",), True, 2)
    lines = [f"[配乐 · 最近 {RECENT_PROJECTS} 个其他项目，按 film.html 修改时间]"]
    lines.extend(f"  {use.project.name}: {use.source}" for use in history)
    if not history:
        lines.append("  暂无已识别的近期配乐。")
    duplicates: list[str] = []
    if taking:
        chosen = Path(taking).name
        duplicates.extend(f"{chosen} 已用于 {use.project.name}" for use in history
                          if use.identity == chosen or Path(urlsplit(use.source).path).name == chosen)
    layered: list[str] = []
    for index, use in enumerate(uses):
        if any(abs(use.at - earlier.at) < 0.000001
               and ((use.digest and use.digest == earlier.digest)
                    or (use.identity and use.identity == earlier.identity)
                    or use.source == earlier.source) for earlier in uses[:index]):
            layered.append(f"BLOCK {use.source} 在 {use.at:g} 秒重复叠加，请移除多余音乐轨道。")
        matches = [previous.project.name for previous in history
                   if (use.digest and use.digest == previous.digest)
                   or (use.identity and use.identity == previous.identity)
                   or (not use.digest and use.source == previous.source)]
        if matches:
            duplicates.append(f"{use.source} 与 {', '.join(matches)} 重复")
        if not use.digest:
            lines.append(f"WARN 远程配乐只能比较 URL，未核验音频内容：{use.source}")
    if layered:
        return MusicResult(tuple(forwarded), tuple(lines + layered), True, 2)
    if duplicates:
        lines.extend(f"{'ALLOW' if reason else 'BLOCK'} {message}" for message in duplicates)
        if not reason:
            lines.append("请重新选曲；用户指定复用时加 --allow-music-reuse '具体原因'。")
            return MusicResult(tuple(forwarded), tuple(lines), True, 2)
    if reason:
        lines.append(f"ALLOW 配乐复用原因：{reason}")
    if checking:
        lines.append(f"PASS 配乐去重（识别 {len(uses)} 条音乐；不代表已完成听感、响度和时长验收）。")
    handled = request.command == "get" and args[0] in {"music-check", "music-history"}
    return MusicResult(tuple(forwarded), tuple(lines), handled)
