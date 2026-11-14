"""Persona-owned creation requests and durable generation results."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import assert_never

from easel.ownership import MediaManifestEntry


class GenerationError(ValueError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class GenerationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class ImageRequest(GenerationModel):
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    model: str = Field(min_length=1, max_length=200)
    channel: Literal['', 'openai', 'agnes', 'gemini'] = ''
    imageSize: Literal['', '0.5K', '1K', '2K', '4K'] = ''
    aspectRatio: str = Field(default='', pattern=r'^$|^[1-9][0-9]*:[1-9][0-9]*$')
    operation: Literal['generate', 'edit'] = 'generate'
    image: str | tuple[str, ...] = ()
    mask: str = ''
    n: int = Field(default=1, ge=1, le=10)
    size: str = Field(default='1024x1024', max_length=100)
    quality: str = Field(default='', max_length=100)
    style: str = Field(default='', max_length=100)
    response_format: Literal['url', 'b64_json'] | None = None
    user: str = Field(default='', max_length=200)

    @property
    def images(self) -> tuple[str, ...]:
        match self.image:
            case str() as path:
                return (path,)
            case tuple() as paths:
                return paths
            case unreachable:
                assert_never(unreachable)

    @model_validator(mode='after')
    def check_inputs(self) -> ImageRequest:
        if not self.prompt.strip():
            raise GenerationError('generation_prompt_required')
        if any('\0' in value for value in (self.prompt, self.model, self.size, self.quality, self.style, self.user)):
            raise GenerationError('generation_text_invalid')
        references = (*self.images, *((self.mask,) if self.mask else ()))
        if len(self.images) > 16 or any(not path or len(path) > (70 * 1024**2 if path[:5].lower().startswith('data:') else 4096)
                                       for path in references):
            raise GenerationError('generation_edit_inputs_invalid')
        if any('\0' in path for path in references):
            raise GenerationError('generation_edit_inputs_invalid')
        for path in references:
            is_url = path[:8].lower().startswith(('http://', 'https://')) or path.startswith('/api/media/')
            if is_url and any(ord(character) < 32 for character in path):
                raise GenerationError('generation_edit_inputs_invalid')
        match self.operation:
            case 'generate':
                if self.image or self.mask:
                    raise GenerationError('generation_edit_inputs_unexpected')
            case 'edit':
                if not self.image or self.n != 1:
                    raise GenerationError('generation_edit_inputs_invalid')
                if self.style or self.quality:
                    raise GenerationError('generation_edit_options_invalid')
            case unreachable:
                assert_never(unreachable)
        return self


class ImageEditRequest(ImageRequest):
    operation: Literal['generate', 'edit'] = 'edit'


class GeneratedAsset(GenerationModel):
    path: str
    sha256: str
    size: int
    kind: Literal['image', 'video'] = 'image'


class ImageUsage(GenerationModel):
    inputTokenDetails: dict[str, float] = Field(default_factory=dict)
    outputTokenDetails: dict[str, float] = Field(default_factory=dict)
    input_tokens: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_tokens: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_tokens: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ImageReceipt(GenerationModel):
    model: str = ''
    provider: str = ''
    created: float = Field(ge=0, allow_inf_nan=False)
    revised_prompts: tuple[str, ...] = ()
    usage: ImageUsage | None = None
    background: str = ''
    output_format: str = ''
    quality: str = ''
    size: str = ''


class ImageResultItem(GeneratedAsset):
    url: str
    revised_prompt: str = ''


class ImageResult(GenerationModel):
    model: str = ''
    provider: str = ''
    created: float
    images: tuple[ImageResultItem, ...] = Field(serialization_alias='list')
    usage: ImageUsage | None = None
    background: str = ''
    output_format: str = ''
    quality: str = ''
    size: str = ''


GenerationState = Literal['queued', 'running', 'submitting', 'waiting', 'succeeded', 'failed', 'unknown']
GenerationStatus = Literal['generating', 'success', 'failed']


class GenerationJob(GenerationModel):
    id: str
    persona: str
    state: GenerationState
    kind: Literal['image'] = Field(default='image', serialization_alias='type')
    channel: str = 'new-api'
    request: ImageRequest
    inputs: tuple[MediaManifestEntry, ...] = ()
    runtime_model: str = ''
    created_at: float
    updated_at: float
    assets: tuple[GeneratedAsset, ...] = ()
    response: ImageResult | None = None
    error: str = ''


class GenerationSummary(GenerationModel):
    id: str
    persona: str
    state: GenerationState
    kind: Literal['image', 'video', 'chat', 'agent', 'aideo', 'draft-generation', 'character'] = Field(serialization_alias='type')
    channel: str
    model: str
    operation: Literal['generate', 'edit', 'create', 'remix', 'completion', 'stream', 'claude', 'claude_completion',
                        'agent', 'aideo', 'video', 'image-text', 'video-url', 'video-edit']
    created_at: float
    updated_at: float


class GenerationPage(GenerationModel):
    items: tuple[GenerationSummary, ...]
    total: int
    page: int
    page_size: int


class GeneratedAssetPage(GenerationModel):
    items: tuple[GeneratedAsset, ...]
    total: int
    page: int
    page_size: int
