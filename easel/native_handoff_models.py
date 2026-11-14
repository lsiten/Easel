"""Owner-reviewed native-app templates and visitor action contracts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool

from easel._channel_publish_models import LocalMedia
from easel.publish_context import PublishContext

HandoffPlatform = Literal['douyin-open', 'rednote-link']


class HandoffTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    persona: str
    request_key: str = Field(min_length=1, max_length=200)
    title: str = Field(default='', max_length=1000)
    body: str = Field(default='', max_length=100000)
    media: tuple[LocalMedia, ...] = Field(min_length=1, max_length=32)
    options: dict[str, JsonValue] = Field(default_factory=dict)
    context: PublishContext = Field(default_factory=PublishContext)
    reviewed: StrictBool = False
    expires_in: int = Field(default=3600, ge=60, le=86400)


class HandoffStart(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    request_key: str = Field(min_length=1, max_length=200)
    reviewed: StrictBool = False


class ShareConfigRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    persona: str
    nonce: str = Field(default='', max_length=64)
