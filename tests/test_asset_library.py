"""每账号素材库引擎（skills/shared/scripts/assets.py）的行为测试。

覆盖：登记/注销源目录、基线整理（类型+关键词）、逐件 place 归类、
按主题检索、素材树、能力状态，以及物理移动与非法画像拒绝。
"""
from pathlib import Path

import json
import importlib.util
import shutil
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "shared" / "scripts" / "assets.py"


def _load():
    spec = importlib.util.spec_from_file_location("asset_lib_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def assets(tmp_path, monkeypatch):
    m = _load()
    # 把引擎的画像目录与登记清单指向临时目录，隔离真实 profiles/。
    monkeypatch.setattr(m, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(m, "REGISTRY", tmp_path / "asset_scopes.json")
    return m


def _mk_source(root: Path) -> Path:
    src = root / "src"
    src.mkdir()
    (src / "嘉兴本地.md").write_text("# 嘉兴南湖\n嘉兴本地城市内容\n", encoding="utf-8")
    (src / "plugin.md").write_text("# AI Plugin\n一个 GPT Agent 插件产品\n", encoding="utf-8")
    (src / "clip.mp4").write_bytes(b"\x00\x01hello video")
    (src / "cover.png").write_bytes(b"\x89PNG\r\n")
    return src


def test_add_and_sources(assets, tmp_path):
    src = _mk_source(tmp_path)
    r = assets.add("测试画像", [str(src)])
    assert str(src) in r["added"]
    assert assets.sources("测试画像") == [str(src)]
    # 重复登记记为 already，不重复
    r2 = assets.add("测试画像", [str(src)])
    assert r2["already"] == [str(src)] and not r2["added"]


def test_remove(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    r = assets.remove("测试画像", str(src))
    assert r["removed"] is True
    assert assets.sources("测试画像") == []


def test_organize_baseline_categorizes(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    r = assets.organize("测试画像")
    assert r["placed"] == 4
    # 无 Agent 时只按类型进入待确认，不把关键词猜测冒充语义整理。
    items = assets.list_items("测试画像")
    cats = {x["name"]: x["category"] for x in items}
    assert cats["嘉兴本地.md"] == "99-待确认/文本"
    assert cats["plugin.md"] == "99-待确认/文本"
    assert cats["clip.mp4"] == "99-待确认/视频"
    assert cats["cover.png"] == "99-待确认/图片"
    dst = tmp_path / "测试画像" / "assets" / "99-待确认" / "文本" / "嘉兴本地.md"
    assert dst.is_file() and not dst.is_symlink()
    assert not (src / "嘉兴本地.md").exists()


def test_organize_dry_run_does_not_place(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    r = assets.organize("测试画像", dry_run=True)
    assert r["placed"] == 4
    assert not (tmp_path / "测试画像" / "assets").is_dir()


def test_place_with_tags_and_summary(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    r = assets.place("测试画像", str(src / "嘉兴本地.md"), "活动", tags=["春节", "本地"],
                      summary="嘉兴春节活动", kind="text")
    assert r["ok"] is True
    item = next(x for x in assets.list_items("测试画像") if x["name"] == "嘉兴本地.md")
    assert item["category"] == "活动"
    assert set(item["tags"]) == {"春节", "本地"}
    assert item["summary"] == "嘉兴春节活动"


def test_manual_asset_correction_survives_later_ai_organization(assets, tmp_path):
    source = _mk_source(tmp_path) / "cover.png"
    assets.organize("测试画像", plan=[{
        "file": str(source), "category": "图片", "tags": ["auto"], "summary": "auto",
    }])
    entries = assets.list_items("测试画像")
    assert len(entries) == 1
    assert Path(entries[0]["source"]).is_file()
    assert Path(entries[0]["original_source"]) == source

    stored = entries[0]["source"]
    assets.update_entry("测试画像", stored, category="品牌与Logo", tags=["确认"],
                        location="嘉兴", project="禾城AI造物局")
    assets.organize("测试画像", plan=[{
        "file": (tmp_path / "测试画像/assets/品牌与Logo/cover.png").as_posix(),
        "category": "其他", "tags": ["new"], "summary": "new",
    }])

    item = next(x for x in assets.list_items("测试画像") if x["name"] == "cover.png")
    assert item["category"] == "品牌与Logo"
    assert item["tags"] == ["确认"]
    assert item["location"] == "嘉兴"
    assert item["project"] == "禾城AI造物局"
    assert item["manual"] is True


def test_changed_only_scan_returns_new_or_modified_sources(assets, tmp_path):
    source = _mk_source(tmp_path)
    assets.add("测试画像", [str(source)])
    first = assets.scan("测试画像", changed_only=True)
    assets.organize("测试画像", plan=[{
        "file": item["path"], "category": "图片" if item["kind"] == "image" else "文件",
        "kind": item["kind"], "tags": [], "summary": item["name"],
    } for item in first])
    assert assets.scan("测试画像", changed_only=True) == []

    (source / "new.md").write_text("new", encoding="utf-8")
    changed = assets.scan("测试画像", changed_only=True)

    assert [item["name"] for item in changed] == ["new.md"]


def test_query_matches_topic(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    assets.organize("测试画像")
    hits = assets.query("测试画像", "嘉兴")
    names = [x["name"] for x in hits]
    assert "嘉兴本地.md" in names
    # 按类型检索
    videos = assets.query("测试画像", "", kind="video")
    assert [x["name"] for x in videos] == ["clip.mp4"]


def test_check_json(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    assets.organize("测试画像")
    j = assets.check_json("测试画像")
    assert j["registered"] is True
    assert j["organized"] == 4
    assert j["categories"] == 3  # 待确认/文本、视频、图片


def test_tree_for_library(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    assets.organize("测试画像")
    tree = assets.tree_for_library("测试画像")
    assert tree is not None and tree["assets"] is True and tree["name"] == "素材库"
    names = {c["name"] for c in tree["children"]}
    assert names == {"99-待确认"}
    # 跳过内部元文件（_index.json / _meta.json / manifest.json）
    assert all(not c["name"].startswith("_") for c in tree["children"])


def test_indexed_assets_stay_visible_when_their_source_disappears(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    assets.organize("测试画像")
    shutil.rmtree(src)

    status = assets.check_json("测试画像")
    tree = assets.tree_for_library("测试画像")

    assert status["registered"] is False
    assert status["organized"] == 4
    assert str(src) in status["missing_sources"]
    assert tree is not None


def test_invalid_persona_rejected(assets, tmp_path):
    src = _mk_source(tmp_path)
    with pytest.raises(ValueError):
        assets.add("../escape", [str(src)])
    assert assets.sources("非法规画/名") == []


def test_same_named_sources_remain_distinct_after_repeated_organize(assets, tmp_path):
    sources = [tmp_path / name for name in ("first", "second")]
    for index, source in enumerate(sources):
        source.mkdir()
        (source / "photo.png").write_bytes(f"image-{index}".encode())
    assets.add("测试画像", [str(source) for source in sources])

    assets.organize("测试画像")
    assets.organize("测试画像")

    entries = assets.list_items("测试画像")
    assert len(entries) == 2
    assert {entry["name"] for entry in entries} == {"photo.png"}
    category = tmp_path / "测试画像/assets/99-待确认/图片"
    files = [
        path for path in category.iterdir()
        if path.is_file() and not path.name.startswith("_") and path.name != "manifest.json"
    ]
    assert all(not path.is_symlink() for path in files)
    assert {path.read_bytes() for path in files} == {b"image-0", b"image-1"}
    assert all(not (source / "photo.png").exists() for source in sources)


def test_reorganize_updates_metadata_and_removes_previous_category_link(assets, tmp_path):
    source = _mk_source(tmp_path) / "cover.png"
    assets.place("测试画像", str(source), "旧分类", tags=["old"], summary="old")
    stored = assets.list_items("测试画像")[0]["source"]

    assets.organize("测试画像", plan=[{
        "file": stored, "category": "新分类", "tags": ["new"], "summary": "new summary",
    }])

    entries = assets.list_items("测试画像")
    assert len(entries) == 1
    assert entries[0]["category"] == "新分类"
    assert entries[0]["tags"] == ["new"]
    assert entries[0]["summary"] == "new summary"
    assert not (tmp_path / "测试画像/assets/旧分类/cover.png").exists()
    assert not source.exists()
    assert (tmp_path / "测试画像/assets/新分类/cover.png").read_bytes() == b"\x89PNG\r\n"
    assert json.loads((tmp_path / "测试画像/assets/旧分类/manifest.json").read_text()) == []


def test_legacy_asset_link_is_replaced_by_the_physical_file(assets, tmp_path):
    source = _mk_source(tmp_path) / "cover.png"
    library = tmp_path / "测试画像/assets"
    category = library / "图片"
    category.mkdir(parents=True)
    destination = category / "cover.png"
    destination.symlink_to(source)
    entry = {
        "name": "cover.png", "kind": "image", "category": "图片",
        "tags": [], "summary": "legacy", "source": str(source),
        "mtime": int(source.stat().st_mtime), "stored_name": "cover.png",
    }
    (category / "manifest.json").write_text(json.dumps([entry]), encoding="utf-8")
    (library / "_index.json").write_text(json.dumps([entry]), encoding="utf-8")

    assets.organize("测试画像", plan=[{
        "file": str(source), "category": "图片", "tags": ["physical"], "summary": "moved",
    }])

    assert not destination.is_symlink()
    assert destination.read_bytes() == b"\x89PNG\r\n"
    assert not source.exists()
    stored = assets.list_items("测试画像")[0]
    assert stored["source"] == str(destination)
    assert stored["original_source"] == str(source)


@pytest.mark.parametrize("category", ["../outside", "absolute", "nested/../../outside"])
def test_unsafe_categories_are_rejected_without_placing(assets, tmp_path, category):
    source = _mk_source(tmp_path) / "cover.png"
    target = str(tmp_path / "outside") if category == "absolute" else category
    with pytest.raises(ValueError):
        assets.place("测试画像", str(source), target)
    assert assets.list_items("测试画像") == []


def test_category_symlink_cannot_escape_library(assets, tmp_path):
    source = _mk_source(tmp_path) / "cover.png"
    outside = tmp_path / "outside"
    outside.mkdir()
    library = tmp_path / "测试画像/assets"
    library.mkdir(parents=True)
    (library / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        assets.organize("测试画像", plan=[{"file": str(source), "category": "escape/nested"}])

    assert list(outside.iterdir()) == []
    assert assets.list_items("测试画像") == []


def test_place_resolves_alias_and_moves_the_real_source_once(assets, tmp_path):
    source = _mk_source(tmp_path) / "cover.png"
    alias = tmp_path / "alias.png"
    alias.symlink_to(source)
    result = assets.place("测试画像", str(alias), "图片", tags=["updated"], summary="updated")

    entries = assets.query("测试画像", "updated")
    assert len(assets.list_items("测试画像")) == 1
    assert entries == [result["entry"]]
    assert entries[0]["source"] == str(Path(result["path"]).resolve())
    assert entries[0]["name"] == "cover.png"
    links = list((tmp_path / "测试画像/assets/图片").glob("*.png"))
    assert links == [Path(result["path"])]
    assert not source.exists()
    assert Path(result["path"]).read_bytes() == b"\x89PNG\r\n"


def test_metadata_write_failure_restores_previous_library(assets, tmp_path, monkeypatch):
    import asset_storage
    source = _mk_source(tmp_path) / "cover.png"
    original = assets.place("测试画像", str(source), "旧分类", tags=["old"])
    index = assets.list_items("测试画像")
    write = asset_storage._write_json

    def fail_index(path, entries):
        if path.name == "_index.json":
            raise OSError("simulated disk write failure")
        write(path, entries)

    monkeypatch.setattr(asset_storage, "_write_json", fail_index)
    with pytest.raises(OSError, match="simulated disk write failure"):
        assets.place("测试画像", index[0]["source"], "新分类", tags=["new"])

    assert assets.list_items("测试画像") == index
    assert Path(original["path"]).is_file()
    manifest = tmp_path / "测试画像/assets/旧分类/manifest.json"
    assert json.loads(manifest.read_text()) == index
    assert not (tmp_path / "测试画像/assets/新分类/cover.png").exists()


def test_reserved_metadata_filename_does_not_replace_manifest(assets, tmp_path):
    source = tmp_path / "manifest.json"
    source.write_text('{"original": true}')

    result = assets.place("测试画像", str(source), "文本")

    manifest = tmp_path / "测试画像/assets/文本/manifest.json"
    assert json.loads(manifest.read_text()) == [result["entry"]]
    assert Path(result["path"]).read_text() == '{"original": true}'
    assert Path(result["path"]) != manifest


def test_library_tree_counts_real_files_recursively_and_hides_retired_categories(assets, tmp_path):
    source = _mk_source(tmp_path)
    assets.add("测试画像", [str(source)])
    assets.place("测试画像", str(source / "cover.png"), "旧分类/子分类")
    stored = assets.list_items("测试画像")[0]["source"]
    assets.place("测试画像", stored, "新分类/子分类")
    assets.place("测试画像", str(source / "plugin.md"), "新分类")
    category = tmp_path / "测试画像/assets/新分类"
    (category / "_index.json").write_text("[]")
    (category / "_meta.json").write_text("{}")
    (category / "missing.png").symlink_to(source / "missing.png")

    tree = assets.tree_for_library("测试画像")

    assert [(entry["name"], entry["fileCount"]) for entry in tree["children"]] == [("新分类", 2)]
