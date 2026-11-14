"""Reuse stable project classifications so AI only sees new or changed files."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from easel.project_organization_models import (
    ROLE_DIRECTORIES,
    ProjectDecision,
    ProjectFile,
    ProjectRole,
)


class StoredOrganizationFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    path: str
    role: ProjectRole
    confidence: float = Field(ge=0, le=1)
    reason: str
    sha256: str = ""


class StoredOrganization(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    files: tuple[StoredOrganizationFile, ...] = ()


def _role_for_category(path: str) -> ProjectRole | None:
    for role, category in ROLE_DIRECTORIES.items():
        if role is ProjectRole.UNKNOWN:
            continue
        if path == category or path.startswith(category + "/"):
            return role
    return None


def reusable_decisions(project: Path, files: list[ProjectFile]) -> tuple[ProjectDecision, ...]:
    """Return stable decisions already recorded or already placed in canonical folders."""
    try:
        raw = json.loads((project / ".easel.json").read_text(encoding="utf-8"))
        organization = StoredOrganization.model_validate(raw.get("organization", {}))
    except (OSError, ValueError):
        organization = StoredOrganization()
    by_path = {item.path: item for item in files}
    decisions: dict[str, ProjectDecision] = {}
    recorded_paths: set[str] = set()
    for record in organization.files:
        recorded_paths.add(record.path)
        current = by_path.get(record.path)
        category = ROLE_DIRECTORIES[record.role]
        if (current is None or record.role is ProjectRole.UNKNOWN
                or record.confidence < 0.55
                or not (record.path == category or record.path.startswith(category + "/"))
                or (record.sha256 and record.sha256 != current.sha256)):
            continue
        decisions[record.path] = ProjectDecision(
            file=record.path, role=record.role, confidence=record.confidence,
            reason=record.reason,
        )
    for current in files:
        if current.path in decisions or current.path in recorded_paths:
            continue
        role = _role_for_category(current.path)
        if role is None:
            continue
        decisions[current.path] = ProjectDecision(
            file=current.path, role=role, confidence=0.99,
            reason="沿用既有分类目录",
        )
    return tuple(decisions[item.path] for item in files if item.path in decisions)


def record_current_hashes(project: Path, files: list[ProjectFile],
                          decisions: tuple[ProjectDecision, ...]) -> None:
    """Backfill fingerprints for legacy organizations without calling the Agent."""
    decision_by_path = {decision.file: decision for decision in decisions}
    path = project / ".easel.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    organization = manifest.get("organization")
    if not isinstance(organization, dict):
        organization = {"schemaVersion": 1, "organizedAt": "", "files": []}
    records = {
        item["path"]: dict(item)
        for item in organization.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    current_files = []
    for item in files:
        decision = decision_by_path.get(item.path)
        if decision is None or item.path not in records:
            continue
        record = records[item.path]
        record["sha256"] = item.sha256
        current_files.append(record)
    if not current_files:
        return
    organization["files"] = current_files
    organization["organizedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest["organization"] = organization
    temporary = path.with_suffix(".json.incremental.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
