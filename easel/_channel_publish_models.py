"""Approved local content and durable official-provider workflow state."""
from __future__ import annotations

from typing import Literal, Mapping
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, field_validator

from easel.channels.models import PublishReceipt
from easel.publish_context import PublishContext


class LocalMedia(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    path: str = Field(min_length=1, max_length=2048)
    kind: Literal['image', 'video']


class ChannelScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    persona: str = Field(min_length=1, max_length=100)
    account_uid: str = Field(min_length=1, max_length=512)


class ChannelIntent(ChannelScope):
    title: str = Field(default='', max_length=100000)
    body: str = Field(default='', max_length=100000)
    media: tuple[LocalMedia, ...] = Field(default=(), max_length=35)
    cover_path: str = Field(default='', max_length=2048)
    options: Mapping[str, JsonValue] = Field(default_factory=dict)
    scheduled_for: str = ''
    context: PublishContext = Field(default_factory=PublishContext)

    @field_validator('scheduled_for')
    @classmethod
    def canonical_schedule(cls, value: str) -> str:
        if not value:
            return ''
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('scheduled_for 必须包含时区')
        return parsed.astimezone(timezone.utc).isoformat()


class ChannelPublishRequest(ChannelIntent):
    reviewed: StrictBool = False
    approval_digest: str = Field(min_length=64, max_length=64)
    approval_expires_at: int
    idempotency_key: str = Field(min_length=1, max_length=200)
    retry: StrictBool = False


class ChannelApproval(BaseModel):
    model_config = ConfigDict(frozen=True)
    platform: str
    persona: str
    account_uid: str
    mode: str
    approval_digest: str
    approval_expires_at: int
    media_manifest_hash: str


class ChannelProgress(BaseModel):
    model_config = ConfigDict(frozen=True)
    stage: Literal['media_init', 'media_upload', 'media_poll', 'publish', 'publication_upload', 'publication_poll', 'publication_cover', 'publication_user_action', 'complete']
    frozen_paths: tuple[str, ...] = ()
    uploaded_media: tuple[str, ...] = ()
    media_index: int = 0
    receipt: PublishReceipt | None = None
    inflight: bool = False
    cover_status: Literal['', 'pending', 'applied', 'failed'] = ''
    cover_error: str = ''


class ChannelRunView(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: str
    platform: str
    persona: str
    account_uid: str
    state: str
    stage: str = ''
    platform_work_id: str = ''
    permalink: str = ''
    error: str = ''
    visibility: str = ''
    work_link_status: str = ''
    cover_status: str = ''
    cover_error: str = ''
    requires_user_action: bool = False
    action_url: str = ''
    action_expires_at: float = 0
    created_at: str
    updated_at: str


class ChannelReconcileRequest(ChannelScope):
    platform_work_id: str = Field(default='', max_length=512)
    permalink: str = Field(default='', max_length=2048)


class ChannelReconcileView(ChannelRunView):
    verification_outcome: str
    verification_reason: str = ''


class ReviewedMedia(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal['image', 'video']
    sha256: str
    size: int
    preview_url: str
    role: Literal['media', 'cover'] = 'media'


class ChannelReapproval(ChannelApproval):
    run_id: str
    stage: str
    original_intent: ChannelIntent
    frozen_media: tuple[ReviewedMedia, ...]


class ChannelReapproveRequest(ChannelScope):
    reviewed: StrictBool = False
    approval_digest: str = Field(min_length=64, max_length=64)
    approval_expires_at: int
