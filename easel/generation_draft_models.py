"""Source-compatible draft requests and typed execution checkpoints."""
from __future__ import annotations
from typing import Annotated, Literal, Protocol
from pydantic import Field, HttpUrl, computed_field
from easel.generation_models import GenerationModel, GeneratedAsset
from easel.generation_agent_models import MediaDataURI

OwnedImageReference = Annotated[str, Field(min_length=1, max_length=4096, pattern=r'^(?:/api/media/)?[^:/\\\x00-\x1f]+(?:/[^:/\\\x00-\x1f]+)+$')]
Reference = HttpUrl | MediaDataURI | OwnedImageReference
ContentType = Literal['video', 'image-text']


class DraftInput(GenerationModel):
    quantity: int = Field(default=1, ge=1, le=10, strict=True)
    groupId: str = ''
    prompt: str = Field(default='', max_length=2000)
    captionPrompt: str = Field(default='', max_length=2000)
    platforms: tuple[str, ...] | None = Field(default=None, max_length=30)
    plannerModel: str = Field(default='', max_length=200)
    disableMemory: bool = True


class VideoDraftInput(DraftInput):
    kind: Literal['video'] = 'video'
    model: str = Field(min_length=1, max_length=200)
    duration: int | None = Field(default=None, ge=1, le=15, strict=True)
    resolution: str = Field(default='', max_length=100)
    aspectRatio: Literal['1:1', '16:9', '9:16', '4:3', '3:4', '3:2', '2:3'] | None = None
    imageUrls: tuple[Reference, ...] = Field(default=(), max_length=9)
    videoUrls: tuple[HttpUrl, ...] = Field(default=(), max_length=3)
    audioUrls: tuple[HttpUrl, ...] = Field(default=(), max_length=3)
    draftType: Literal['draft', 'video'] = 'draft'


class ImageDraftInput(DraftInput):
    kind: Literal['image-text'] = 'image-text'
    prompt: str = Field(max_length=2000)
    imageModel: str = Field(min_length=1, max_length=200)
    imageCount: int = Field(default=3, ge=1, le=9, strict=True)
    imageSize: str = Field(default='', max_length=100)
    aspectRatio: Literal['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9'] | None = None
    imageUrls: tuple[Reference, ...] = Field(default=(), max_length=14)
    draftType: Literal['draft', 'image'] = 'draft'


class URLDraftInput(DraftInput):
    kind: Literal['video-url'] = 'video-url'
    quantity: Literal[1] = 1
    videoUrl: HttpUrl
    draftType: Literal['draft'] = 'draft'


DraftRequest = Annotated[VideoDraftInput | ImageDraftInput | URLDraftInput, Field(discriminator='kind')]


class DraftSelection(GenerationModel):
    taskIds: tuple[str, ...] = Field(min_length=1, max_length=10)


class DraftPlan(GenerationModel):
    title: str = Field(default='', max_length=200)
    description: str = Field(default='', max_length=2200)
    topics: tuple[str, ...] = Field(default=(), max_length=5)
    videoPrompt: str = Field(default='', max_length=4000)
    imagePrompts: tuple[Annotated[str, Field(min_length=1, max_length=1000)], ...] = Field(default=(), max_length=9)


class DraftMedia(GenerationModel):
    asset: GeneratedAsset
    thumbnail: str = ''
    generationId: str = ''


class DraftResult(GenerationModel):
    materialId: str = ''
    mediaIds: tuple[str, ...] = ()
    title: str = ''
    description: str = ''
    topics: tuple[str, ...] = ()
    imageUrls: tuple[str, ...] = ()
    videoUrl: str = ''
    coverUrl: str = ''
    plan: DraftPlan | None = None


class DraftJob(GenerationModel):
    id: str
    persona: str
    request_key: str
    request: DraftRequest
    state: Literal['queued', 'running', 'succeeded', 'failed', 'unknown'] = 'queued'
    createdAt: float
    updatedAt: float
    plan: DraftPlan | None = None
    media: tuple[DraftMedia, ...] = ()
    response: DraftResult | None = None
    errorMessage: str = ''

    @computed_field
    @property
    def status(self) -> Literal['generating', 'success', 'failed']:
        return 'generating' if self.state in ('queued', 'running') else 'success' if self.state == 'succeeded' else 'failed'


class DraftPage(GenerationModel):
    items: tuple[DraftJob, ...]
    total: int
    page: int
    pageSize: int


class DraftCreated(GenerationModel):
    taskIds: tuple[str, ...]


class DraftStats(GenerationModel):
    generatingCount: int


class MemoryItem(GenerationModel):
    id: str
    text: str = Field(min_length=1, max_length=120)
    createdAt: float
    updatedAt: float


class DraftMemory(GenerationModel):
    id: str
    userId: str
    contentType: ContentType
    items: tuple[MemoryItem, ...] = ()
    lastGeneratedAt: float
    sampleCount: int
    createdAt: float
    updatedAt: float


class MemoryRegenerate(GenerationModel):
    contentType: ContentType
    plannerModel: str = ''


class MemoryText(GenerationModel):
    text: str = Field(min_length=1, max_length=120)


class MemoryResult(GenerationModel):
    items: tuple[MemoryText, ...] = Field(max_length=20)


class MemoryRun(GenerationModel):
    persona: str
    request_key: str
    model: str
    contentType: ContentType
    samples: str


class ModelRate(GenerationModel):
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str = 'USD'
    unit: Literal['image', 'second', 'video']
    resolution: str = ''


class DraftModelPrice(GenerationModel):
    model: str
    name: str = ''
    displayName: str = ''
    description: str = ''
    channel: str = ''
    supportedAspectRatios: tuple[str, ...] = ()
    maxInputImages: int | None = None
    resolutions: tuple[str, ...] = ()
    durations: tuple[float, ...] = ()
    aspectRatios: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    pricing: tuple[ModelRate, ...] = ()


class DraftPricing(GenerationModel):
    imageModels: tuple[DraftModelPrice, ...]
    imageEditModels: tuple[DraftModelPrice, ...] = ()
    videoModels: tuple[DraftModelPrice, ...]


class DraftRuntime(Protocol):
    def validate(self, persona: str, payload: DraftRequest) -> None: ...
    async def plan(self, job: DraftJob, memory: tuple[str, ...]) -> DraftPlan: ...
    async def media(self, job: DraftJob, position: int) -> DraftMedia: ...
    async def memory(self, run: MemoryRun) -> MemoryResult: ...
    def pricing(self, persona: str) -> DraftPricing: ...
