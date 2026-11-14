"""Validated local media, draft, group, and query contracts."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue

Domain = Literal['media', 'material']
ContentKind = Literal['image', 'video', 'audio', 'article', 'text']


class ContentError(ValueError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class ContentScope(FrozenModel):
    persona: str
    domain: Domain


class GroupInput(FrozenModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default='', max_length=4000)
    kind: ContentKind | None = None
    platforms: tuple[str, ...] = ()


class ContentGroup(GroupInput):
    id: str
    persona: str
    domain: Domain
    is_default: bool = False
    created_at: str


class MediaReference(FrozenModel):
    path: str
    media_id: str = ''
    thumbnail_path: str = ''
    kind: ContentKind = 'image'
    content: str = Field(default='', max_length=100_000)


class RecordInput(FrozenModel):
    group_id: str
    title: str = Field(default='', max_length=1000)
    description: str = Field(default='', max_length=100_000)
    kind: ContentKind
    path: str = ''
    thumbnail_path: str = ''
    media: tuple[MediaReference, ...] = ()
    topics: tuple[str, ...] = ()
    options: dict[str, JsonValue] = Field(default_factory=dict)
    status: Literal['draft', 'success', 'error'] = 'success'
    max_use_count: int | None = Field(default=None, ge=1)
    account_types: tuple[str, ...] = ()
    material_id: str = ''
    auto_delete_media: bool = False


class ContentRecord(RecordInput):
    id: str
    persona: str
    domain: Domain
    use_count: int = 0
    created_at: str
    updated_at: str


class ContentFilter(FrozenModel):
    title: str = ''
    group_id: str = ''
    material_group_id: str = ''
    kind: ContentKind | None = None
    status: Literal['draft', 'success', 'error'] | None = None
    min_use_count: int = Field(default=0, ge=0)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=200)


class RecordPage(FrozenModel):
    items: tuple[ContentRecord, ...]
    total: int
    page: int
    page_size: int


class GroupView(ContentGroup):
    thumbnails: tuple[str, ...] = ()


class GroupPage(FrozenModel):
    items: tuple[GroupView, ...]
    total: int
    page: int
    page_size: int


class IdSelection(FrozenModel):
    ids: tuple[str, ...] = Field(min_length=1, max_length=1000)


class TransferInput(IdSelection):
    target_group_id: str
    mode: Literal['move', 'copy']


class UsageInput(IdSelection):
    run_id: str = Field(min_length=1, max_length=200)


class RemoteImportInput(FrozenModel):
    request_id: str = Field(min_length=1, max_length=200)
    group_id: str
    url: str = Field(min_length=1, max_length=4096)
    kind: Literal['image', 'video', 'audio']
    title: str = Field(default='', max_length=1000)
    description: str = Field(default='', max_length=100_000)
