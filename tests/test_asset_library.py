"""每账号素材库引擎（skills/shared/scripts/assets.py）的行为测试。

覆盖：登记/注销源目录、基线整理（类型+关键词）、逐件 place 归类、
按主题检索、素材树、能力状态，以及软链落位与非法画像拒绝。
"""
from pathlib import Path

import json
import importlib.util
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
    # 主题桶优先：嘉兴本地.md→嘉兴本地，plugin.md→AI与产品；类型兜底：clip.mp4→视频，cover.png→图片
    items = assets.list_items("测试画像")
    cats = {x["name"]: x["category"] for x in items}
    assert cats["嘉兴本地.md"] == "嘉兴本地"
    assert cats["plugin.md"] == "AI与产品"
    assert cats["clip.mp4"] == "视频"
    assert cats["cover.png"] == "图片"
    # 落位为软链（指向源目录，不复制大文件）
    dst = tmp_path / "测试画像" / "assets" / "嘉兴本地" / "嘉兴本地.md"
    assert dst.is_file() and dst.is_symlink()
    assert dst.resolve() == src / "嘉兴本地.md"


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
    assert j["categories"] == 4  # 嘉兴本地/AI与产品/视频/图片


def test_tree_for_library(assets, tmp_path):
    src = _mk_source(tmp_path)
    assets.add("测试画像", [str(src)])
    assets.organize("测试画像")
    tree = assets.tree_for_library("测试画像")
    assert tree is not None and tree["assets"] is True and tree["name"] == "素材库"
    names = {c["name"] for c in tree["children"]}
    assert "嘉兴本地" in names and "AI与产品" in names
    # 跳过内部元文件（_index.json / _meta.json / manifest.json）
    assert all(not c["name"].startswith("_") for c in tree["children"])


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
    category = tmp_path / "测试画像/assets/图片"
    links = [path for path in category.iterdir() if path.is_symlink()]
    assert {path.resolve() for path in links} == {source / "photo.png" for source in sources}
    assert {path.read_bytes() for path in links} == {b"image-0", b"image-1"}


def test_reorganize_updates_metadata_and_removes_previous_category_link(assets, tmp_path):
    source = _mk_source(tmp_path) / "cover.png"
    assets.place("测试画像", str(source), "旧分类", tags=["old"], summary="old")

    assets.organize("测试画像", plan=[{
        "file": str(source), "category": "新分类", "tags": ["new"], "summary": "new summary",
    }])

    entries = assets.list_items("测试画像")
    assert len(entries) == 1
    assert entries[0]["category"] == "新分类"
    assert entries[0]["tags"] == ["new"]
    assert entries[0]["summary"] == "new summary"
    assert not (tmp_path / "测试画像/assets/旧分类/cover.png").exists()
    assert source.read_bytes() == b"\x89PNG\r\n"
    assert json.loads((tmp_path / "测试画像/assets/旧分类/manifest.json").read_text()) == []


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


def test_place_resolves_alias_and_replaces_metadata_in_same_category(assets, tmp_path):
    source = _mk_source(tmp_path) / "cover.png"
    alias = tmp_path / "alias.png"
    alias.symlink_to(source)
    assets.place("测试画像", str(source), "图片", tags=["old"])

    result = assets.place("测试画像", str(alias), "图片", tags=["updated"], summary="updated")

    entries = assets.query("测试画像", "updated")
    assert len(assets.list_items("测试画像")) == 1
    assert entries == [result["entry"]]
    assert entries[0]["source"] == str(source.resolve())
    links = list((tmp_path / "测试画像/assets/图片").glob("*.png"))
    assert links == [Path(result["path"])]
    assert source.read_bytes() == b"\x89PNG\r\n"


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
        assets.place("测试画像", str(source), "新分类", tags=["new"])

    assert assets.list_items("测试画像") == index
    assert Path(original["path"]).resolve() == source
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
    assets.place("测试画像", str(source / "cover.png"), "新分类/子分类")
    assets.place("测试画像", str(source / "plugin.md"), "新分类")
    category = tmp_path / "测试画像/assets/新分类"
    (category / "_index.json").write_text("[]")
    (category / "_meta.json").write_text("{}")
    (category / "missing.png").symlink_to(source / "missing.png")

    tree = assets.tree_for_library("测试画像")

    assert [(entry["name"], entry["fileCount"]) for entry in tree["children"]] == [("新分类", 2)]
