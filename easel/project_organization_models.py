"""Typed project organization inventory, decisions, and durable job state."""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectRole(StrEnum):
    DELIVERABLE = "deliverable"
    PROJECT_INFO = "project_info"
    COPY = "copy"
    SCRIPT = "script"
    BRAND_VISUAL = "brand_visual"
    VISUAL = "visual"
    COVER = "cover"
    AUDIO = "audio"
    PAGE = "page"
    CODE = "code"
    STYLE = "style"
    PRODUCTION_CONFIG = "production_config"
    PUBLISH = "publish"
    CHECK = "check"
    VERSION = "version"
    UNKNOWN = "unknown"


ROLE_DIRECTORIES: dict[ProjectRole, str] = {
    ProjectRole.DELIVERABLE: "00-成品",
    ProjectRole.PROJECT_INFO: "01-项目资料",
    ProjectRole.COPY: "02-文案",
    ProjectRole.SCRIPT: "03-脚本与分镜",
    ProjectRole.BRAND_VISUAL: "04-视觉素材/品牌与Logo",
    ProjectRole.VISUAL: "04-视觉素材/图片与视频",
    ProjectRole.COVER: "04-视觉素材/封面",
    ProjectRole.AUDIO: "05-音频",
    ProjectRole.PAGE: "06-制作工程/页面",
    ProjectRole.CODE: "06-制作工程/代码",
    ProjectRole.STYLE: "06-制作工程/样式",
    ProjectRole.PRODUCTION_CONFIG: "06-制作工程/配置",
    ProjectRole.PUBLISH: "07-发布记录",
    ProjectRole.CHECK: "08-检查与版本/检查记录",
    ProjectRole.VERSION: "08-检查与版本/历史版本",
    ProjectRole.UNKNOWN: "99-待确认",
}

PROJECT_ROOT_FILES = frozenset({".easel.json", "film.html"})
PROJECT_ROOT_DIRS = frozenset({".film"})


class ProjectFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    kind: Literal["image", "video", "audio", "text", "other"]
    size: int = Field(ge=0)
    mtime: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        if not value or value.startswith("/") or ".." in value.split("/"):
            raise ValueError(  # noqa: GENERIC_ERR_OK — required by Pydantic validators.
                "project file path must stay inside the project",
            )
        return value


class ProjectDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    file: str
    role: ProjectRole
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class ProjectPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ProjectDecision, ...]


class ProjectMove(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    target: str
    role: ProjectRole
    confidence: float
    reason: str
    sha256: str


class ProjectOrganizationJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    profile: str
    projects: tuple[str, ...]
    status: Literal["queued", "running", "done", "failed", "rolled_back"] = "queued"
    total: int = Field(ge=0)
    completed: int = Field(default=0, ge=0)
    moved: int = Field(default=0, ge=0)
    reused: int = Field(default=0, ge=0)
    resume_from: int = Field(default=0, ge=0)
    current_project: str = ""
    rollback_available: bool = False
    transactions: tuple[str, ...] = ()
    error: str = ""
    updated: float
    owner_pid: int = 0
