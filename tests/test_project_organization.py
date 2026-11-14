"""Physical project organization keeps references valid and can be rolled back."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from easel.project_organization_models import (
    ProjectDecision,
    ProjectPlan,
    ProjectRole,
)
from easel.project_organization import ProjectOrganizer
from easel.project_organization_plan import (
    ProjectOrganizationError,
    build_moves,
    inventory_project,
    parse_project_plan,
)
from easel.project_organization_transaction import (
    apply_project_plan,
    rollback_transaction,
)
import web.app as web


def _project(tmp_path: Path) -> tuple[Path, Path]:
    outputs = tmp_path / "outputs"
    project = outputs / "作品"
    (project / "assets").mkdir(parents=True)
    (project / ".film").mkdir()
    (project / ".film" / "state.json").write_text("{}", encoding="utf-8")
    (project / "film.html").write_text(
        '<iframe src="page.html"></iframe><img src="assets/logo.png">',
        encoding="utf-8",
    )
    (project / "page.html").write_text(
        '<link href="theme.css"><img src="assets/logo.png">',
        encoding="utf-8",
    )
    (project / "theme.css").write_text("body{background:url('assets/logo.png')}", encoding="utf-8")
    (project / "scene.js").write_text(
        "const footer = '<img src=\"assets/logo.png\">';",
        encoding="utf-8",
    )
    (project / "assets" / "logo.png").write_bytes(b"png")
    (project / "发布文案.txt").write_text("正文", encoding="utf-8")
    (project / ".easel.json").write_text(json.dumps({
        "profile": "测试画像",
        "title": "作品",
        "kind": "video",
        "cover": "assets/logo.png",
        "deliverables": ["page.html"],
        "steps": [{"outputs": ["发布文案.txt"], "upstream": ["assets/logo.png"]}],
    }), encoding="utf-8")
    return outputs, project


def _moves(project: Path, roles: dict[str, ProjectRole]):
    files = inventory_project(project)
    plan = ProjectPlan(items=tuple(
        ProjectDecision(file=path, role=roles[path], confidence=0.95, reason="按实际内容判断")
        for path in roles
    ))
    return build_moves(project, files, plan)


def test_project_organization_moves_files_rewrites_references_and_rolls_back(tmp_path):
    outputs, project = _project(tmp_path)
    moves = _moves(project, {
        "page.html": ProjectRole.PAGE,
        "theme.css": ProjectRole.STYLE,
        "scene.js": ProjectRole.CODE,
        "assets/logo.png": ProjectRole.BRAND_VISUAL,
        "发布文案.txt": ProjectRole.COPY,
    })

    transaction = apply_project_plan(outputs, project, "测试画像", moves)

    assert (project / "06-制作工程/页面/page.html").is_file()
    assert (project / "06-制作工程/样式/theme.css").is_file()
    assert (project / "06-制作工程/代码/scene.js").is_file()
    assert (project / "04-视觉素材/品牌与Logo/logo.png").is_file()
    assert (project / "02-文案/发布文案.txt").is_file()
    assert (project / "film.html").is_file()
    assert (project / ".film/state.json").is_file()
    film = (project / "film.html").read_text(encoding="utf-8")
    assert "06-制作工程/页面/page.html" in film
    assert "04-视觉素材/品牌与Logo/logo.png" in film
    page = (project / "06-制作工程/页面/page.html").read_text(encoding="utf-8")
    assert "../样式/theme.css" in page
    assert "../../04-视觉素材/品牌与Logo/logo.png" in page
    script = (project / "06-制作工程/代码/scene.js").read_text(encoding="utf-8")
    assert '../../04-视觉素材/品牌与Logo/logo.png' in script
    manifest = json.loads((project / ".easel.json").read_text(encoding="utf-8"))
    assert manifest["cover"] == "04-视觉素材/品牌与Logo/logo.png"
    assert manifest["deliverables"] == ["06-制作工程/页面/page.html"]
    assert manifest["organization"]["files"]

    rollback_transaction(outputs, transaction)

    assert (project / "page.html").is_file()
    assert (project / "assets/logo.png").is_file()
    assert '<iframe src="page.html">' in (project / "film.html").read_text(encoding="utf-8")


def test_project_inventory_keeps_engine_and_hidden_state_in_place(tmp_path):
    _, project = _project(tmp_path)

    paths = {item.path for item in inventory_project(project)}

    assert paths == {"page.html", "theme.css", "scene.js", "assets/logo.png", "发布文案.txt"}


def test_project_organization_is_idempotent(tmp_path):
    outputs, project = _project(tmp_path)
    roles = {
        "page.html": ProjectRole.PAGE,
        "theme.css": ProjectRole.STYLE,
        "scene.js": ProjectRole.CODE,
        "assets/logo.png": ProjectRole.BRAND_VISUAL,
        "发布文案.txt": ProjectRole.COPY,
    }
    apply_project_plan(outputs, project, "测试画像", _moves(project, roles))

    second = _moves(project, {
        "06-制作工程/页面/page.html": ProjectRole.PAGE,
        "06-制作工程/样式/theme.css": ProjectRole.STYLE,
        "04-视觉素材/品牌与Logo/logo.png": ProjectRole.BRAND_VISUAL,
        "02-文案/发布文案.txt": ProjectRole.COPY,
    })

    assert all(move.source == move.target for move in second)


def test_same_named_identical_files_receive_distinct_targets(tmp_path):
    outputs, project = _project(tmp_path)
    (project / "assets" / "second").mkdir()
    (project / "assets" / "second" / "logo.png").write_bytes(b"png")
    files = inventory_project(project)
    plan = ProjectPlan(items=tuple(
        ProjectDecision(
            file=item.path,
            role=ProjectRole.BRAND_VISUAL if item.path.endswith("logo.png") else ProjectRole.COPY,
            confidence=0.9,
            reason="测试同名冲突",
        )
        for item in files
    ))

    moves = build_moves(project, files, plan)
    targets = [move.target for move in moves]

    assert len(targets) == len(set(targets))
    apply_project_plan(outputs, project, "测试画像", moves)
    brands = sorted(path.name for path in (project / "04-视觉素材/品牌与Logo").iterdir())
    assert brands == ["logo-8f8cbb7d.png", "logo.png"]


def test_historical_version_stays_as_a_working_snapshot(tmp_path):
    outputs = tmp_path / "outputs"
    project = outputs / "作品"
    version = project / "revisions" / "第一版"
    version.mkdir(parents=True)
    (project / ".easel.json").write_text('{"profile":"测试画像"}', encoding="utf-8")
    (version / "film.html").write_text('<link href="theme.css"><iframe src="page.html">', encoding="utf-8")
    (version / "page.html").write_text('<link href="theme.css">', encoding="utf-8")
    (version / "theme.css").write_text("body{background:#fff}", encoding="utf-8")
    files = inventory_project(project)
    plan = ProjectPlan(items=tuple(
        ProjectDecision(file=item.path, role=ProjectRole.PAGE, confidence=0.9, reason="版本快照")
        for item in files
    ))

    moves = build_moves(project, files, plan)
    apply_project_plan(outputs, project, "测试画像", moves)

    target = project / "08-检查与版本/历史版本/第一版"
    assert (target / "film.html").is_file()
    assert (target / "page.html").is_file()
    assert (target / "theme.css").is_file()
    assert 'href="theme.css"' in (target / "page.html").read_text(encoding="utf-8")
    assert all(move.role == ProjectRole.VERSION for move in moves)


def test_project_plan_rejects_partial_ai_output(tmp_path):
    _, project = _project(tmp_path)
    files = inventory_project(project)

    with pytest.raises(ProjectOrganizationError, match="project_plan_incomplete"):
        parse_project_plan('{"items":[]}', files)


def test_project_plan_accepts_harmless_kind_metadata_from_agent(tmp_path):
    _, project = _project(tmp_path)
    files = inventory_project(project)
    response = json.dumps({"items": [
        {
            "file": item.path,
            "kind": item.kind,
            "role": "unknown",
            "confidence": 0.4,
            "reason": "Agent 增加了素材整理流程里的 kind 字段",
        }
        for item in files
    ]})

    plan = parse_project_plan(response, files)

    assert {item.file for item in plan.items} == {item.path for item in files}


def test_organizer_scopes_projects_and_runs_the_ai_plan(tmp_path):
    outputs, project = _project(tmp_path)
    other = outputs / "别人的作品"
    other.mkdir()
    (other / ".easel.json").write_text('{"profile":"别的画像"}', encoding="utf-8")
    (other / "keep.txt").write_text("keep", encoding="utf-8")

    def runner(prompt: str, profile: str, session: str) -> str:
        assert profile == "测试画像"
        assert "发布文案.txt" in prompt
        return json.dumps({"items": [
            {"file": "page.html", "role": "page", "confidence": 0.99, "reason": "影片页面"},
            {"file": "theme.css", "role": "style", "confidence": 0.99, "reason": "页面样式"},
            {"file": "scene.js", "role": "code", "confidence": 0.99, "reason": "页面脚本"},
            {"file": "assets/logo.png", "role": "brand_visual", "confidence": 0.99, "reason": "品牌标识"},
            {"file": "发布文案.txt", "role": "copy", "confidence": 0.99, "reason": "发布文案"},
        ]})

    organizer = ProjectOrganizer(outputs, runner)
    job = organizer.start("测试画像")
    organizer.run(job)
    finished = organizer.current("测试画像")

    assert finished is not None and finished.status == "done"
    assert finished.projects == ("作品",)
    assert finished.moved == 5
    assert (other / "keep.txt").is_file()


def test_failed_run_retries_only_remaining_projects_and_preserves_transactions(tmp_path):
    outputs, first = _project(tmp_path)
    second = outputs / "第二部作品"
    shutil.copytree(first, second)
    valid = json.dumps({"items": [
        {"file": "page.html", "role": "page", "confidence": 0.99, "reason": "页面"},
        {"file": "theme.css", "role": "style", "confidence": 0.99, "reason": "样式"},
        {"file": "scene.js", "role": "code", "confidence": 0.99, "reason": "脚本"},
        {"file": "assets/logo.png", "role": "brand_visual", "confidence": 0.99, "reason": "标识"},
        {"file": "发布文案.txt", "role": "copy", "confidence": 0.99, "reason": "文案"},
    ]})
    calls: list[str] = []

    def fail_second(prompt: str, profile: str, session: str) -> str:
        calls.append(prompt)
        return valid if "第二部作品" not in prompt else "not-json"

    organizer = ProjectOrganizer(outputs, fail_second)
    organizer.run(organizer.start("测试画像"))
    failed = organizer.current("测试画像")
    assert failed is not None and failed.status == "failed"
    assert (failed.completed, len(failed.transactions)) == (1, 1)
    transaction = failed.transactions[0]

    organizer.runner = lambda prompt, profile, session: valid
    resumed = organizer.start("测试画像")

    assert (resumed.id, resumed.resume_from, resumed.transactions) == (
        failed.id, 1, failed.transactions,
    )
    assert resumed.current_project == "第二部作品"
    organizer.run(resumed)
    finished = organizer.current("测试画像")
    assert finished is not None and finished.status == "done"
    assert (finished.completed, finished.moved, len(finished.transactions)) == (2, 10, 2)
    assert transaction in finished.transactions
    assert all("作品" in prompt for prompt in calls)


def test_real_web_app_exposes_project_organization_route():
    client = TestClient(web.app, base_url="http://127.0.0.1")

    response = client.get("/api/projects/organize")

    assert response.status_code == 400
    assert response.json()["detail"] == "请先选择有效的账号画像"
