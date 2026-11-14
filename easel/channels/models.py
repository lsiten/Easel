"""Typed OAuth credentials and platform publishing contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

PlatformId: TypeAlias = Literal['youtube', 'tiktok', 'instagram', 'facebook', 'threads', 'twitter', 'linkedin', 'pinterest', 'google-business',
    'bilibili-open', 'douyin-open', 'kwai-open', 'wechat-official-open', 'wechat-channels-open', 'rednote-link']
JsonDocument: TypeAlias = dict[str, JsonValue]


class ChannelError(RuntimeError):
    def __init__(self, platform: str, code: str, detail: str = '', status: int = 400) -> None:
        self.platform = platform
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(str(self))

    def __str__(self) -> str:
        return f'{self.platform}: {self.code}' + (f' ({self.detail})' if self.detail else '')


@dataclass(frozen=True, slots=True)
class PlatformDefinition:
    platform: PlatformId
    authorize_url: str
    token_url: str
    api_base: str
    scopes: tuple[str, ...]
    scope_separator: str = ' '
    token_auth: Literal['body', 'basic', 'client_key', 'graph'] = 'body'
    pkce: bool = False
    publish_modes: tuple[str, ...] = ()
    max_text: int = 0
    max_images: int = 0
    max_videos: int = 0
    completion: Literal['sync', 'polling', 'none', 'user_handoff'] = 'none'
    works: bool = False
    analytics: bool = False
    engagement: bool = False
    auth_mode: Literal['oauth2', 'miniapp_qr', 'app_handoff'] = 'oauth2'


class OAuthApplication(BaseModel):
    model_config = ConfigDict(frozen=True)
    client_id: str = Field(min_length=1)
    client_secret: SecretStr = SecretStr('')
    redirect_uri: str
    scopes: tuple[str, ...] = ()


class Credential(BaseModel):
    model_config = ConfigDict(frozen=True)
    persona: str = Field(min_length=1)
    platform: PlatformId
    account_uid: str = Field(min_length=1)
    access_token: SecretStr
    refresh_token: SecretStr = SecretStr('')
    expires_at: float | None = None
    scope: str = ''
    display_name: str = ''
    parent_uid: str = ''
    profile: AccountProfile | None = None
    auxiliary_tokens: JsonDocument = Field(default_factory=dict, exclude=True, repr=False)


class AccountProfile(BaseModel):
    model_config = ConfigDict(frozen=True)
    account_uid: str
    display_name: str = ''
    avatar_url: str = ''
    parent_uid: str = ''
    username: str = ''
    email: str = ''
    fans_count: int | None = None
    following_count: int | None = None
    extra: JsonDocument = Field(default_factory=dict)


class MediaInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal['image', 'video']
    url: str = ''
    media_id: str = ''
    metadata: MediaMetadata | None = None


class MediaMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)
    size_bytes: int | None = Field(default=None, ge=0)
    duration_sec: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    format: str = ''


class PublishContent(BaseModel):
    model_config = ConfigDict(frozen=True)
    title: str = ''
    body: str = ''
    media: tuple[MediaInput, ...] = ()
    options: Mapping[str, JsonValue] = Field(default_factory=dict)


class PublishReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)
    state: Literal['published', 'pending', 'requires_upload', 'requires_finalize', 'requires_user_action', 'failed']
    external_request_id: str = ''
    platform_work_id: str = ''
    permalink: str = ''
    upload_url: str = ''
    details: JsonDocument = Field(default_factory=dict)


class OAuthPending(BaseModel):
    model_config = ConfigDict(frozen=True)
    persona: str
    platform: PlatformId
    verifier: SecretStr
    redirect_uri: str
    expires_at: float
