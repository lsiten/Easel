"""Manifest ownership shared by project writers and the output library."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError


class ProjectOwnership(BaseModel):
    """Parse the owner while preserving the rest of the manifest on update."""

    model_config = ConfigDict(frozen=True, extra="allow")
    profile: StrictStr = ""


def in_persona_scope(project: Path, persona: str | None) -> bool:
    """General mode browses all projects; named accounts require explicit ownership."""
    if not persona:
        return True
    try:
        manifest = ProjectOwnership.model_validate_json((project / ".easel.json").read_bytes())
    except (OSError, ValidationError):
        return False
    return manifest.profile == persona


def record_profile(project: Path, persona: str) -> None:
    """Fill missing ownership atomically without changing an existing owner or steps."""
    if not persona:
        return
    path = project / ".easel.json"
    if path.exists():
        manifest = ProjectOwnership.model_validate_json(path.read_bytes())
        if manifest.profile:
            return
        manifest = manifest.model_copy(update={"profile": persona})
    else:
        manifest = ProjectOwnership(profile=persona, topic=project.name, steps=[])
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=project,
                                     prefix=".easel-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(manifest.model_dump_json(indent=2, exclude_unset=True) + "\n")
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
