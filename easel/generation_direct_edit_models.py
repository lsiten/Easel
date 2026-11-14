"""MIT DirectEdit track contracts adapted to persona-owned Easel jobs."""
from typing import Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, model_validator
from easel.generation_models import GeneratedAsset, GenerationError


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class CanvasParam(Model):
    Width: int = Field(gt=0)
    Height: int = Field(gt=0)
    BackgroundColor: str | None = None


class CodecParam(Model):
    VideoCodec: str | None = None
    AudioCodec: str | None = None
    VideoBitrate: float | None = Field(None, validation_alias=AliasChoices('VideoBitrate', 'VideoBitRate'))
    AudioBitrate: float | None = None
    Crf: float | None = Field(None, ge=0, le=51)
    Preset: str | None = None


class OutputParam(Model):
    Fps: float | None = Field(None, gt=0)
    DisableVideo: bool | None = None
    DisableAudio: bool | None = None
    Alpha: bool | None = None
    Codec: CodecParam | None = None


class TrackElement(BaseModel):
    """Keep native extensible resource/filter fields without stripping provider options."""
    model_config = ConfigDict(frozen=True, extra='allow')
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)
    Type: str = Field(min_length=1)
    Source: str | None = None
    TargetTime: tuple[float, float] | None = None
    SourceTime: tuple[float, float] | None = None
    Extra: tuple[dict[str, JsonValue], ...] | None = None


class UploadParam(Model):
    SpaceName: str = Field(min_length=1)
    VideoName: str | None = None
    FileName: str | None = None


class DirectEditParam(Model):
    Canvas: CanvasParam | None = None
    Output: OutputParam | None = None
    Track: tuple[tuple[TrackElement, ...], ...] = Field(min_length=1)
    Upload: UploadParam | None = None
    Uploader: str | None = None


class DirectEditRequest(Model):
    persona: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=1, max_length=200)
    Canvas: CanvasParam | None = None
    Output: OutputParam | None = None
    Track: tuple[tuple[TrackElement, ...], ...] | None = Field(None, min_length=1)
    EditParam: DirectEditParam | None = None
    SpaceName: str = ''
    Uploader: str = ''
    Application: str = Field('VideoTrackToB', min_length=1)
    Priority: int | None = Field(None, ge=0, le=9)
    CallbackUrl: str | None = None
    CallbackArgs: str | None = Field(None, max_length=65536)

    @model_validator(mode='after')
    def request_shape(self) -> 'DirectEditRequest':
        if self.EditParam is None and self.Track is None:
            raise GenerationError('direct_edit_track_required', 422)
        if self.EditParam is not None and any(value is not None for value in (self.Track, self.Canvas, self.Output)):
            raise GenerationError('direct_edit_params_conflict', 422)
        return self

    def edit(self) -> DirectEditParam:
        if self.EditParam is not None:
            return self.EditParam
        if self.Track is None:
            raise GenerationError('direct_edit_track_required', 422)
        return DirectEditParam(Canvas=self.Canvas, Output=self.Output, Track=self.Track)


class DirectEditReceipt(Model):
    ReqId: str = ''
    TaskId: str = ''
    Status: str = Field(min_length=1)
    OutputVid: str = ''
    OutputUrl: str = ''
    Message: str = ''


class DirectEditJob(Model):
    id: str
    persona: str
    state: Literal['running', 'succeeded', 'failed', 'unknown'] = 'unknown'
    channel: Literal['volcengine'] = 'volcengine'
    request_model: Literal['video-edit'] = 'video-edit'
    operation: Literal['video-edit'] = 'video-edit'
    provider_id: str = ''
    space: str
    request: DirectEditRequest
    response: DirectEditReceipt | None = None
    assets: tuple[GeneratedAsset, ...] = ()
    error: str = 'direct_edit_submission_in_progress_or_interrupted'
    created_at: float
    updated_at: float
