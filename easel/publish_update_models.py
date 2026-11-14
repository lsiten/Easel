"""Reviewed metadata edits attached to a server-owned published task."""
from __future__ import annotations

from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool

from easel._channel_publish_models import ChannelScope
from easel.ownership import MediaManifestEntry


class UpdateIntent(ChannelScope):
    title: str = Field(default='', max_length=100)
    body: str = Field(default='', max_length=5000)
    options: Mapping[str, JsonValue] = Field(default_factory=dict)
    cover_path: str = Field(default='', max_length=2048)


class UpdateRequest(UpdateIntent):
    reviewed: StrictBool = False
    request_key: str = Field(min_length=1, max_length=200)
    viewed_revision: str = Field(min_length=64, max_length=64)
    approval_digest: str = Field(min_length=64, max_length=64)
    approval_expires_at: int


class UpdateApproval(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str
    run_id: str
    platform: str
    platform_work_id: str
    current: Mapping[str, JsonValue]
    viewed_revision: str
    approval_digest: str
    approval_expires_at: int
    cover_sha256: str = ''


class PublicationUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)
    update_id: str
    task_id: str
    run_id: str
    platform: str
    platform_work_id: str
    intent: UpdateIntent
    request_key: str
    approval_digest: str
    viewed_revision: str
    credential_revision: str
    cover_manifest: tuple[MediaManifestEntry, ...] = ()
    state: Literal['prepared', 'pending', 'succeeded', 'unknown'] = 'prepared'
    stage: Literal['metadata', 'cover', 'complete'] = 'metadata'
    inflight: bool = False
    metadata_applied: bool = False
    cover_status: Literal['', 'pending', 'applied', 'failed'] = ''
    error: str = ''
    created_at: str
    updated_at: str


class UpdateHistory(BaseModel):
    model_config = ConfigDict(frozen=True)
    records: Mapping[str, PublicationUpdate] = Field(default_factory=dict)


class UpdateView(BaseModel):
    model_config = ConfigDict(frozen=True)
    update_id: str
    task_id: str
    run_id: str
    platform: str
    platform_work_id: str
    state: str
    stage: str
    metadata_applied: bool
    cover_status: str
    error: str
    created_at: str
    updated_at: str
