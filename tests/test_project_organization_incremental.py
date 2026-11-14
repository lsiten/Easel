"""Incremental project organization avoids repeat AI work and cleans moved sources."""
from __future__ import annotations

import json
from pathlib import Path

from easel.project_organization import ProjectOrganizer
from easel.project_organization_models import (
    ProjectDecision,
    ProjectPlan,
    ProjectRole,
)
from easel.project_organization_plan import (
    build_moves,
    inventory_project,
    parse_project_plan,
)
from easel.project_organization_transaction import (
    apply_project_plan,
    rollback_transaction,
)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    outputs = tmp_path / "outputs"
    project = outputs / "作品"
    (project / "assets").mkdir(parents=True)
    (project / "assets" / "logo.png").write_bytes(b"png")
    (project / "page.html").write_text('<img src="assets/logo.png">', encoding="utf-8")
    (project / "发布文案.txt").write_text("正文", encoding="utf-8")
    (project / "film.html").write_text('<iframe src="page.html"></iframe>', encoding="utf-8")
    (project / ".easel.json").write_text(
        json.dumps({"profile": "测试画像", "cover": "assets/logo.png"}),
        encoding="utf-8",
    )
    return outputs, project


def _response(*, page_role: ProjectRole = ProjectRole.PAGE) -> str:
    return json.dumps({"items": [
        {"file": "page.html", "kind": "other", "role": page_role.value,
         "confidence": 0.99, "reason": "页面"},
        {"file": "assets/logo.png", "role": "brand_visual",
         "confidence": 0.99, "reason": "品牌标识"},
        {"file": "发布文案.txt", "role": "copy",
         "confidence": 0.99, "reason": "发布文案"},
    ]}, ensure_ascii=False)


def test_second_organize_skips_agent_when_all_files_are_unchanged(tmp_path):
    outputs, _ = _project(tmp_path)
    calls = 0

    def runner(prompt: str, profile: str, session: str) -> str:
        nonlocal calls
        calls += 1
        return _response()

    organizer = ProjectOrganizer(outputs, runner)
    organizer.run(organizer.start("测试画像"))
    first = organizer.current("测试画像")
    assert first is not None
    manifest_path = outputs / "作品/.easel.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["organization"]["files"]:
        item.pop("sha256", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def unexpected(prompt: str, profile: str, session: str) -> str:
        raise AssertionError("unchanged organized files should not call the Agent again")

    organizer.runner = unexpected
    organizer.run(organizer.start("测试画像"))
    finished = organizer.current("测试画像")

    assert calls == 1
    assert finished is not None and finished.status == "done"
    assert finished.moved == 0
    assert finished.reused == 3
    assert finished.transactions == first.transactions
    assert finished.rollback_available is True
    refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert all(item.get("sha256") for item in refreshed["organization"]["files"])


def test_modified_file_is_the_only_item_sent_to_agent(tmp_path):
    outputs, project = _project(tmp_path)
    organizer = ProjectOrganizer(outputs, lambda prompt, profile, session: _response())
    organizer.run(organizer.start("测试画像"))
    (project / "06-制作工程/页面/page.html").write_text(
        '<img src="../../04-视觉素材/品牌与Logo/logo.png"><p>改动</p>',
        encoding="utf-8",
    )
    prompts: list[str] = []

    def changed_runner(prompt: str, profile: str, session: str) -> str:
        prompts.append(prompt)
        return json.dumps({"items": [
            {"file": "06-制作工程/页面/page.html", "role": "page",
             "confidence": 0.99, "reason": "内容已更新"},
        ]}, ensure_ascii=False)

    organizer.runner = changed_runner
    organizer.run(organizer.start("测试画像"))

    assert len(prompts) == 1
    assert "06-制作工程/页面/page.html" in prompts[0]
    assert "发布文案.txt" not in prompts[0]


def test_pretty_printed_agent_plan_is_parsed(tmp_path):
    _, project = _project(tmp_path)
    files = inventory_project(project)
    response = f"""分析完成。
{{
  "items": [
    {{"file": "page.html", "role": "page", "confidence": 0.99, "reason": "页面"}},
    {{"file": "assets/logo.png", "role": "brand_visual", "confidence": 0.99, "reason": "标识"}},
    {{"file": "发布文案.txt", "role": "copy", "confidence": 0.99, "reason": "文案"}}
  ]
}}
"""

    plan = parse_project_plan(response, files)

    assert len(plan.items) == 3


def test_organization_removes_sources_left_empty_and_rollback_recreates_them(tmp_path):
    outputs, project = _project(tmp_path)
    files = inventory_project(project)
    plan = ProjectPlan(items=(
        ProjectDecision(file="page.html", role=ProjectRole.PAGE,
                        confidence=0.99, reason="页面"),
        ProjectDecision(file="assets/logo.png", role=ProjectRole.BRAND_VISUAL,
                        confidence=0.99, reason="标识"),
        ProjectDecision(file="发布文案.txt", role=ProjectRole.COPY,
                        confidence=0.99, reason="文案"),
    ))

    transaction = apply_project_plan(outputs, project, "测试画像", tuple(build_moves(project, files, plan)))

    assert not (project / "assets").exists()
    rollback_transaction(outputs, transaction)
    assert (project / "assets/logo.png").is_file()
