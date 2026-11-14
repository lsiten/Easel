"""Validated task inputs and the boundary to Easel's existing Harness."""
from __future__ import annotations

from typing import Annotated, Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, field_validator


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class TextBlock(Model):
    type: Literal['text']
    text: str = Field(min_length=1, max_length=100000)


# Encoded bound is 16 MiB; decoded content is verified by the runtime attachment bridge.
MediaDataURI = Annotated[str, Field(
    strict=True, max_length=16 * 1024 * 1024,
    pattern=r'^data:(?:image/[A-Za-z0-9.+-]{1,64}|video/[A-Za-z0-9.+-]{1,64}|application/pdf);base64,(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{4}|[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)$',
)]


class URLSource(Model):
    type: Literal['url']
    url: HttpUrl | MediaDataURI


class ImageBlock(Model):
    type: Literal['image']
    source: URLSource


class VideoBlock(Model):
    type: Literal['video']
    source: URLSource


class TextSource(Model):
    type: Literal['text']
    media_type: Literal['text/plain']
    data: str = Field(max_length=100000)


class ContentSource(Model):
    type: Literal['content']
    content: str | tuple[Annotated[TextBlock | ImageBlock, Field(discriminator='type')], ...]


class CacheControl(Model):
    type: Literal['ephemeral']


class DocumentBlock(Model):
    type: Literal['document']
    source: Annotated[URLSource | TextSource | ContentSource, Field(discriminator='type')]
    cache_control: CacheControl | None = None


ContentBlock = Annotated[TextBlock | ImageBlock | VideoBlock | DocumentBlock, Field(discriminator='type')]
Prompt = Annotated[str, Field(min_length=1, max_length=4000)] | Annotated[tuple[ContentBlock, ...], Field(min_length=1, max_length=20)]
TaskStatus = Literal['running', 'completed', 'requires_action', 'error', 'aborted']


class CreateTask(Model):
    prompt: Prompt
    model: str = Field(default='', max_length=200)
    includePartialMessages: bool = False
    taskId: str | None = Field(default=None, max_length=64)

    @field_validator('taskId')
    @classmethod
    def normalized_task_id(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class RenameTask(Model):
    title: str = Field(min_length=1, max_length=200)


class RateTask(Model):
    rating: int = Field(ge=1, le=5, strict=True)
    comment: str = Field(default='', max_length=1000)


class ShareTask(Model):
    ttlSeconds: int = Field(default=604800, ge=60, le=2592000, strict=True)


class AgentRun(Model):
    persona: str
    task_id: str
    turn_id: str
    prompt: Prompt
    model: str = ''
    session_id: str = ''


class AgentEvent(Model):
    """Stable UUID and native Easel event payload; poll replays the durable turn."""
    uuid: str
    type: str
    data: JsonValue = None


class AgentSnapshot(Model):
    events: tuple[AgentEvent, ...] = ()
    status: TaskStatus


class AgentHooks(Protocol):
    """start returns after launch; poll is durable and UUID-idempotent, not consuming."""
    async def start(self, run: AgentRun) -> None: ...
    async def poll(self, run: AgentRun) -> AgentSnapshot: ...
    async def stop(self, run: AgentRun) -> None: ...
    async def delete(self, run: AgentRun) -> None: ...


class AgentTask(Model):
    id: str
    userId: str
    title: str
    status: TaskStatus
    createdAt: float
    updatedAt: float
    rating: int | None = None
    ratingComment: str = ''
    favoritedAt: float | None = None
    messages: tuple[AgentEvent, ...] = ()


class TaskMessages(Model):
    messages: tuple[AgentEvent, ...]
    status: TaskStatus


class TaskPage(Model):
    items: tuple[AgentTask, ...]
    total: int
    page: int
    pageSize: int


class PublicShare(Model):
    token: str
    expiresAt: float


class PublicMessage(Model):
    type: Literal['user', 'assistant']
    text: str


class PublicTask(Model):
    id: str
    title: str
    status: TaskStatus
    messages: tuple[PublicMessage, ...]


class TaskUpdate(Model):
    title: str | None = None
    rating: int | None = None
    ratingComment: str | None = None
    favoritedAt: float | None = None
