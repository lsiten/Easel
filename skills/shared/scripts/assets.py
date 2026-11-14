# noqa: SIZE_OK — compatibility CLI facade; storage, evidence, and physical moves are split modules.
"""assets.py — 每账号（画像）「素材库」引擎：登记素材源目录 → 按内容理解 AI 整理 → 供视频/文章创作取用。

设计（与 Easel 既有 kb / openfilm 模式一致）：
- 每个画像可登记若干「素材源目录」（图片 / 视频 / 文本），用户只需添加目录即可。
  登记清单存 profiles/asset_scopes.json（{画像: [绝对源目录]}，与 kb_scopes.json 同构）。
- 素材库整理树落在 profiles/<画像>/assets/：按内容理解归入分类目录，
  文件本体会从登记源目录物理移动落位，配 manifest.json（分类/类型/标签/摘要/来源）与 _index.json（全量索引）。
- 「按理解的内容整理」由 Agent 驱动：Agent 读文件（文本直读 / 图片看 / 视频转写）理解后
  逐件 `place` 归类；`organize` 无计划时做「类型 + 文件名/内容关键词」基线整理，供可独立运行/自检。
- 创作取用：视频/文章 skill 调 `query` 按主题/类型检索相关素材（随画像，仅本画像素材库）。

用法：
    python assets.py sources <画像>                 # 已登记素材源目录
    python assets.py add <画像> <目录> [更多目录…]   # 登记素材源目录
    python assets.py remove <画像> <目录>           # 注销
    python assets.py scan <画像> [--json]          # 扫描源目录待整理文件（不改动）
    python assets.py organize <画像> [--dry-run]   # 基线整理（类型+关键词）；Agent 可逐件 place 精修
    python assets.py place <画像> --file F --category C [--tags a,b] [--summary ...] [--kind image|video|text]
    python assets.py list <画像> [--category C] [--json]
    python assets.py query <画像> <主题/关键词> [--kind image|video|text] [--limit N] [--json]
    python assets.py check [--json]              # 能力/状态（当前画像是否已配素材库）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_storage import category_directory, read_index as _read_index, store_entry, _write_json as _write_storage_json
from asset_evidence import extract_evidence, sha256_file
from video_run_manifest import is_public_artifact

GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[0;33m"
NC = "\033[0m"


class AssetLibraryError(ValueError):
    """An asset command input violates the persona or catalog contract."""


# ── 路径与画像校验（与 output_paths / persona 一致）──────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROFILES_DIR = PROJECT_ROOT / "profiles"
REGISTRY = PROFILES_DIR / "asset_scopes.json"

_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp", ".tiff", ".heic", ".ico"}
_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".flv", ".ts"}
_TEXT_EXT = {".md", ".txt", ".docx", ".doc", ".pdf", ".csv", ".json", ".yaml", ".yml", ".srt", ".vtt"}
_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}

def _valid_persona(name: str) -> bool:
    return (
        isinstance(name, str) and bool(name)
        and not name.startswith((".", "_"))
        and not any(c in name for c in ("/", "\\", ":"))
        and not any(ord(c) < 32 for c in name)
    )


def _asset_dir(profile: str) -> Path:
    return PROFILES_DIR / profile / "assets"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ── 登记清单（profiles/asset_scopes.json，与 kb_scopes.json 同构）────────────
def load_registry() -> dict:  # noqa: DICT_OK — persisted JSON registry contract.
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_registry(data: dict) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sources(profile: str) -> list[str]:
    """已登记素材源目录（绝对路径、去重、仅存在的目录）。"""
    if not _valid_persona(profile):
        return []
    entry = load_registry().get(profile)
    if not isinstance(entry, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in entry:
        p = str(raw).strip()
        if not p:
            continue
        ap = p if Path(p).is_absolute() else str((PROFILES_DIR / p).resolve())
        r = Path(ap)
        if r.is_dir() and is_public_artifact(r) and ap not in seen:
            seen.add(ap)
            out.append(ap)
    return out


def add(profile: str, dirs: list[str]) -> dict:  # noqa: DICT_OK — CLI/HTTP JSON response.
    """登记素材源目录到 asset_scopes.json（去重、绝对化）。返回 {added, already, invalid}。"""
    if not _valid_persona(profile):
        raise AssetLibraryError(f"非法画像名：{profile!r}")
    reg = load_registry()
    cur = reg.get(profile)
    if not isinstance(cur, list):
        cur = []
    existing = {str(x).strip() for x in cur if str(x).strip()}
    added, already, invalid = [], [], []
    for raw in dirs:
        p = str(raw).strip()
        if not p:
            continue
        ap = p if Path(p).is_absolute() else str(Path(p).expanduser().resolve())
        if not Path(ap).is_dir() or not is_public_artifact(ap):
            invalid.append(ap)
            continue
        if ap in {str(x) for x in existing}:
            already.append(ap)
            continue
        existing.add(ap)
        added.append(ap)
    reg[profile] = sorted(existing)
    save_registry(reg)
    return {"profile": profile, "added": added, "already": already, "invalid": invalid,
            "sources": sources(profile)}


def remove(profile: str, directory: str) -> dict:  # noqa: DICT_OK — CLI/HTTP JSON response.
    if not _valid_persona(profile):
        raise AssetLibraryError(f"非法画像名：{profile!r}")
    reg = load_registry()
    cur = reg.get(profile)
    if not isinstance(cur, list):
        return {"removed": False, "sources": []}
    ap = directory if Path(directory).is_absolute() else str(Path(directory).expanduser().resolve())
    before = len(cur)
    reg[profile] = [x for x in cur if str(x).strip() != ap]
    save_registry(reg)
    return {"removed": len(reg[profile]) != before, "sources": sources(profile)}


# ── 文件类型与基线整理──────────────────────────────────────────────────────
def _kind(path: Path) -> str:
    s = path.suffix.lower()
    if s in _IMG_EXT:
        return "image"
    if s in _VIDEO_EXT:
        return "video"
    if s in _AUDIO_EXT:
        return "audio"
    if s in _TEXT_EXT:
        return "text"
    return "other"


def _baseline_category(kind: str, path: Path) -> str:
    """无 Agent 时只按类型落入待确认，不冒充已完成语义整理。"""
    label = {"image": "图片", "video": "视频", "audio": "音频", "text": "文本"}.get(kind, "其他")
    return f"99-待确认/{label}"


def scan(profile: str, *, changed_only: bool = False) -> list[dict]:
    """扫描已登记源目录内的文件（不改动），返回待整理清单。"""
    items: list[dict] = []
    indexed = {str(Path(item.get("source", "")).resolve()): item for item in list_items(profile)}
    for src in sources(profile):
        for p in sorted(Path(src).rglob("*")):
            if not p.is_file() or p.name.startswith(".") or not is_public_artifact(p):
                continue
            if any(part.startswith(".") for part in p.relative_to(Path(src)).parts):
                continue
            stat = p.stat()
            previous = indexed.get(str(p.resolve()))
            if changed_only and previous and previous.get("mtime") == int(stat.st_mtime):
                continue
            evidence = extract_evidence(p)
            items.append({
                "path": str(p), "name": p.name, "kind": _kind(p),
                "size": stat.st_size, "mtime": int(stat.st_mtime),
                "sha256": evidence["sha256"], "capturedAt": evidence["capturedAt"],
                "locationHint": evidence["locationHint"], "timeSource": evidence["timeSource"],
            })
    return items


# ── 落位 / 整理 / 索引──────────────────────────────────────────────────────
def place(profile: str, file: str, category: str, *, tags: list[str] | None = None,  # noqa: DICT_OK
          summary: str = "", kind: str | None = None, manual: bool = False
          ) -> dict:  # noqa: DICT_OK — CLI/Agent JSON response.
    """把单个素材落位到 profiles/<画像>/assets/<category>/（Agent 逐件调用）。"""
    if not _valid_persona(profile):
        raise AssetLibraryError(f"非法画像名：{profile!r}")
    fp = Path(file).expanduser()
    if not fp.is_file():
        return {"ok": False, "error": f"素材文件不存在：{file}"}
    category = (category or "其他").strip() or "其他"
    kind = kind or _kind(fp)
    asset_dir = _asset_dir(profile)
    source = fp.resolve()
    entry = {
        "name": source.name, "kind": kind, "category": category,
        "tags": [t.strip() for t in (tags or []) if t.strip()],
        "summary": summary.strip()[:500], "source": str(source),
        "mtime": int(source.stat().st_mtime), "stored_name": source.name,
        "sha256": extract_evidence(source)["sha256"], "manual": manual,
    }
    dst, entry = store_entry(asset_dir, entry)
    _write_meta(asset_dir, sources(profile))
    return {"ok": True, "profile": profile, "category": category, "path": str(dst), "entry": entry}


def organize(profile: str, *, dry_run: bool = False,  # noqa: DICT_OK
             plan: list[dict] | None = None) -> dict:  # CLI/HTTP JSON response.
    """整理素材库：plan（Agent 给定的逐件 {file,category,tags,summary,kind}）优先；
    否则用 scan 做「类型 + 文件名/内容关键词」基线整理。dry_run 只返回计划不落位。"""
    if not _valid_persona(profile):
        return {"ok": False, "error": f"非法画像名：{profile!r}"}
    asset_dir = _asset_dir(profile)
    if plan is None:
        plan = [{
            "file": it["path"], "category": _baseline_category(it["kind"], Path(it["path"])),
            "kind": it["kind"], "tags": [Path(it["path"]).stem[:24]], "summary": "",
        } for it in scan(profile)]
    placed, skipped = [], []
    for item in plan:
        category_directory(asset_dir, (item.get("category") or "其他").strip() or "其他")
    for item in plan:
        fp = Path(item.get("file", ""))
        if not fp.is_file():
            skipped.append(str(fp))
            continue
        previous = next((entry for entry in _read_index(asset_dir)
                         if Path(str(entry.get("source", ""))).resolve() == fp.resolve()), None)
        category = (item.get("category") or "其他").strip() or "其他"
        if previous and previous.get("manual"):
            category = str(previous.get("category") or category)
        kind = item.get("kind") or _kind(fp)
        entry = {
            "name": fp.name, "kind": kind, "category": category,
            "tags": [t.strip() for t in (item.get("tags") or []) if t.strip()],
            "summary": (item.get("summary") or "").strip()[:500], "source": str(fp.resolve()),
            "mtime": int(fp.stat().st_mtime), "stored_name": fp.name,
            "sha256": str((item.get("sha256") or sha256_file(fp))),
            "capturedAt": str(item.get("capturedAt") or ""),
            "location": str(item.get("location") or item.get("locationHint") or ""),
            "timeSource": str(item.get("timeSource") or ""),
            "project": str(item.get("project") or ""),
            "role": str(item.get("role") or ""),
            "confidence": float(item.get("confidence") or 0.0),
            "reason": str(item.get("reason") or ""),
            "manual": bool(previous and previous.get("manual")),
        }
        if previous and previous.get("manual"):
            for key in ("tags", "summary", "capturedAt", "location", "timeSource",
                        "project", "role", "confidence", "reason"):
                if key in previous:
                    entry[key] = previous[key]
        if dry_run:
            placed.append(entry)
            continue
        _, stored = store_entry(asset_dir, entry)
        placed.append(stored)
    if not dry_run:
        asset_dir.mkdir(parents=True, exist_ok=True)
        _write_meta(asset_dir, sources(profile))
    return {
        "ok": True, "profile": profile, "dry_run": dry_run,
        "placed": len(placed), "skipped": skipped, "placed_items": placed,
        "dir": str(asset_dir),
    }


def _write_meta(asset_dir: Path, srcs: list[str]) -> None:
    meta = {"sources": srcs, "organized_at": _now_iso(), "index": str(asset_dir / "_index.json")}
    _write_storage_json(asset_dir / "_meta.json", meta)


def update_entry(profile: str, source: str,  # noqa: DICT_OK
                 **changes: str | list[str]) -> dict:  # noqa: DICT_OK — HTTP JSON response.
    """Apply a user-confirmed override and lock it against later AI runs."""
    asset_dir = _asset_dir(profile)
    entries = _read_index(asset_dir)
    resolved = str(Path(source).resolve())
    current = next((entry for entry in entries if str(Path(entry["source"]).resolve()) == resolved), None)
    if current is None:
        raise AssetLibraryError("素材不存在")
    allowed = {"category", "tags", "summary", "capturedAt", "location", "project", "role"}
    updates = {key: value for key, value in changes.items() if key in allowed}
    updated = {**current, **updates, "manual": True}
    store_entry(asset_dir, updated)
    return updated


def list_items(profile: str, category: str | None = None) -> list[dict]:
    items = _read_index(_asset_dir(profile))
    if category:
        items = [x for x in items if x.get("category") == category]
    return items


def query(profile: str, what: str, *, kind: str | None = None, limit: int = 10) -> list[dict]:
    """按主题/关键词检索相关素材（供视频/文章创作 skill 调用；随画像，仅本画像素材库）。
    匹配 category / tags / summary / name / kind 的词法命中，按命中数排序。"""
    items = _read_index(_asset_dir(profile))
    if kind:
        items = [x for x in items if x.get("kind") == kind]
    terms = [t for t in re.split(r"[\s,，、/]+", what.lower()) if t]
    scored: list[tuple[int, dict]] = []
    for x in items:
        hay = " ".join([
            str(x.get("category", "")),
            " ".join(x.get("tags", [])),
            str(x.get("summary", "")),
            str(x.get("name", "")),
            str(x.get("kind", "")),
            str(x.get("capturedAt", "")),
            str(x.get("location", "")),
            str(x.get("project", "")),
            str(x.get("role", "")),
        ]).lower()
        score = sum(hay.count(t) for t in terms)
        if score > 0 or not terms:
            scored.append((score, x))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:limit]]


def tree_for_library(profile: str) -> dict | None:
    """供内容库展示的素材库树（仅分类子目录 + 文件计数），与外部内容源渲染同构。
    只列分类目录，跳过 _index.json / _meta.json 等元文件（以 _ 开头）。"""
    asset_dir = _asset_dir(profile)
    if not asset_dir.is_dir():
        return None
    indexed_counts: dict[str, int] = {}
    for item in _read_index(asset_dir):
        category = str(item.get("category", "")).replace("\\", "/").strip("/")
        if category:
            indexed_counts[category.split("/", 1)[0]] = indexed_counts.get(category.split("/", 1)[0], 0) + 1
    children: list[dict] = []
    for c in sorted(asset_dir.iterdir()):
        if c.name.startswith(".") or c.name.startswith("_") or not c.is_dir():
            continue
        count = max(_count_files(c), indexed_counts.get(c.name, 0))
        if count == 0:
            continue
        fm = {"name": c.name, "type": "dir", "external": False,
             "assets": True, "path": f"@assets/{c.name}",
             "mtime": _mtime(c), "fileCount": count}
        children.append(fm)
    if not children:
        return None
    return {"name": "素材库", "type": "dir", "assets": True, "path": "@assets",
            "children": children, "mtime": _mtime(asset_dir)}


def _mtime(d: Path) -> int:
    try:
        return int(d.stat().st_mtime)
    except OSError:
        return 0


def _count_files(d: Path, cap: int = 100000) -> int:
    n = 0
    try:
        for c in d.iterdir():
            if c.name.startswith(".") or c.name in {"manifest.json", "_index.json", "_meta.json"}:
                continue
            if c.is_dir() and not c.is_symlink():
                n += _count_files(c, cap - n)
            elif c.is_file():
                n += 1
            if n >= cap:
                break
    except OSError:
        return n
    return n


def check_json(profile: str | None = None) -> dict:  # noqa: DICT_OK — CLI/HTTP JSON status.
    """能力/状态：该画像是否已登记素材源、已整理多少素材。"""
    profile = profile or ""
    reg = load_registry()
    entry = reg.get(profile, []) if profile else []
    srcs = sources(profile) if profile else []
    n = len(list_items(profile)) if profile else 0
    cats = {x.get("category") for x in list_items(profile) if x.get("category")} if profile else set()
    missing_sources = [
        str(raw)
        for raw in entry
        if isinstance(raw, str) and raw.strip() and not Path(raw).is_dir()
    ] if profile else []
    return {
        "profile": profile, "registered": bool(srcs), "sources": srcs,
        "registered_paths": len(entry), "missing_sources": missing_sources,
        "registered_all": sum(1 for v in reg.values() if isinstance(v, list) and v),
        "organized": n, "categories": len(cats),
        "asset_dir": str(_asset_dir(profile)) if profile else "",
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Easel 每账号素材库引擎（登记 / 整理 / 检索）")
    ap.add_argument("--json", action="store_true", help="机读 JSON 输出")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("sources"); s.add_argument("profile")
    s = sub.add_parser("add"); s.add_argument("profile"); s.add_argument("dirs", nargs="+")
    s = sub.add_parser("remove"); s.add_argument("profile"); s.add_argument("directory")
    s = sub.add_parser("scan"); s.add_argument("profile")
    s = sub.add_parser("organize"); s.add_argument("profile"); s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("place")
    s.add_argument("profile"); s.add_argument("--file"); s.add_argument("--category")
    s.add_argument("--tags", default=""); s.add_argument("--summary", default="")
    s.add_argument("--kind", default=None)
    s = sub.add_parser("list"); s.add_argument("profile"); s.add_argument("--category", default=None)
    s = sub.add_parser("query")
    s.add_argument("profile"); s.add_argument("what"); s.add_argument("--kind", default=None)
    s.add_argument("--limit", type=int, default=10)
    s = sub.add_parser("check"); s.add_argument("profile", nargs="?", default=None)

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2

    def out(obj) -> int:
        if a.json:
            print(json.dumps(obj, ensure_ascii=False, indent=2))
            return 0
        return 0

    try:
        if a.cmd == "sources":
            r = sources(a.profile)
            if a.json:
                print(json.dumps({"profile": a.profile, "sources": r}, ensure_ascii=False, indent=2)); return 0
            print(f"{a.profile} 已登记素材源目录：")
            for x in r:
                print(f"  {GREEN}✓{NC} {x}")
            if not r:
                print(f"  （未登记，用 {YELLOW}assets.py add {a.profile} <目录>{NC}）")
            return 0
        if a.cmd == "add":
            r = add(a.profile, a.dirs)
            if a.json:
                print(json.dumps(r, ensure_ascii=False, indent=2)); return 0
            print(f"新增 {len(r['added'])} 个素材源目录：")
            for x in r["added"]:
                print(f"  {GREEN}+{NC} {x}")
            for x in r["already"]:
                print(f"  {YELLOW}（已登记）{NC} {x}")
            for x in r["invalid"]:
                print(f"  {RED}✗ 不存在{NC} {x}")
            return 0
        if a.cmd == "remove":
            r = remove(a.profile, a.directory)
            print(f"注销{'成功' if r['removed'] else '（未找到）'}：{a.directory}")
            return 0
        if a.cmd == "scan":
            items = scan(a.profile)
            if a.json:
                print(json.dumps({"profile": a.profile, "files": items}, ensure_ascii=False, indent=2)); return 0
            print(f"待整理素材 {len(items)} 个（类型分布：")
            from collections import Counter
            print("  " + ", ".join(f"{k}:{v}" for k, v in Counter(i['kind'] for i in items).items()))
            return 0
        if a.cmd == "organize":
            r = organize(a.profile, dry_run=a.dry_run)
            if a.json:
                print(json.dumps(r, ensure_ascii=False, indent=2)); return 0
            mode = "（dry-run，未落位）" if a.dry_run else "已落位"
            print(f"整理{mode}：{r['placed']} 个素材 → {r['dir']}")
            for x in r.get("skipped", []):
                print(f"  {RED}跳过（不存在）{NC} {x}")
            return 0
        if a.cmd == "place":
            r = place(a.profile, a.file, a.category, tags=(a.tags.split(",") if a.tags else None),
                      summary=a.summary, kind=a.kind)
            if a.json:
                print(json.dumps(r, ensure_ascii=False, indent=2)); return 0
            if r.get("ok"):
                print(f"{GREEN}✓{NC} 已归类：{r['entry']['name']} → {r['category']}")
            else:
                print(f"{RED}✗{NC} {r.get('error')}")
                return 1
            return 0
        if a.cmd == "list":
            items = list_items(a.profile, a.category)
            if a.json:
                print(json.dumps({"profile": a.profile, "items": items}, ensure_ascii=False, indent=2)); return 0
            print(f"素材库共 {len(items)} 项：")
            for x in items:
                tags = " /".join(x.get("tags", []))
                print(f"  [{x.get('kind')}] {x.get('category')}: {x.get('name')}" + (f"  · {tags}" if tags else ""))
            return 0
        if a.cmd == "query":
            items = query(a.profile, a.what, kind=a.kind, limit=a.limit)
            if a.json:
                print(json.dumps({"profile": a.profile, "query": a.what, "items": items}, ensure_ascii=False, indent=2)); return 0
            print(f"「{a.what}」相关素材 {len(items)} 项：")
            for x in items:
                print(f"  [{x.get('kind')}] {x.get('category')}: {x.get('name')}  · {x.get('summary','')}")
            return 0
        if a.cmd == "check":
            r = check_json(a.profile)
            if a.json:
                print(json.dumps(r, ensure_ascii=False, indent=2)); return 0
            if not a.profile:
                print(f"素材库：已配置画像 {r['registered_all']} 个")
            else:
                state = f"{GREEN}OK{NC}" if r["registered"] else f"{YELLOW}未登记{NC}"
                print(f"素材库（{a.profile}）{state}：源目录 {len(r['sources'])}，已整理 {r['organized']} 项，分类 {r['categories']} 类")
            return 0
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK — CLI boundary.
        print(f"{RED}✗{NC} {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
