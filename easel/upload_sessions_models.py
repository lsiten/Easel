"""Scoped two-phase upload grants and completion contracts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class UploadModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class UploadRequest(UploadModel):
    persona: str
    session_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    expires_in: int = Field(default=900, ge=30, le=3600)
    public: bool = False
    target: Literal['attachment', 'media'] = 'attachment'


class UploadReceipt(UploadModel):
    id: str
    name: str
    path: str


class UploadSession(UploadRequest):
    id: str
    expires_at: int
    state: Literal['prepared', 'uploading', 'uploaded', 'confirmed'] = 'prepared'
    receipt: UploadReceipt | None = None
    receiver_pid: int = 0
    attempt_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')
    attempt_expires_at: float = 0


class UploadGrant(UploadModel):
    id: str
    token: str
    expires_at: int
    upload_url: str
    confirm_url: str


class UploadAccess(UploadModel):
    persona: str = ''
    token: str = ''


@dataclass(frozen=True, slots=True)
class CompletedUpload:
    path: Path
    session: UploadSession
