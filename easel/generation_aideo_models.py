"""Strict Aideo request variants and durable native task receipts."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from easel.generation_models import GeneratedAsset

Skill = Literal['AITranslation', 'Erase', 'Highlight', 'VCreative', 'Vision']

class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

class UrlInput(Model):
    type: Literal['url']
    url: str = Field(min_length=1)
    fileName: str = ''
    fileExtension: str = ''

class StreamInput(Model):
    """JSON transport maps the source stream to a profile-owned media path."""
    type: Literal['stream']
    stream: str = Field(min_length=1)
    fileSize: int = Field(gt=0)
    fileName: str = ''
    fileExtension: str = ''

class VidInput(Model):
    type: Literal['vid']
    vid: str = Field(pattern=r'^[A-Za-z0-9_-]{1,256}$')

class RequestBase(Model):
    persona: str = Field(min_length=1)
    request_key: str = Field(min_length=1, max_length=200)
    multiInputs: tuple[str | Annotated[UrlInput | StreamInput | VidInput, Field(discriminator='type')], ...] = Field(min_length=1, max_length=30)
    spaceName: str = ''

class PromptRequest(RequestBase):
    prompt: str = Field(min_length=1, max_length=32000)

class SkillRequest(RequestBase):
    skillType: Skill
    skillParams: dict[str, JsonValue] | None = None

AideoRequest = PromptRequest | SkillRequest

class WorkflowBase(Model):
    persona: str = Field(min_length=1)
    request_key: str = Field(min_length=1, max_length=200)

class StyleTransferRequest(WorkflowBase):
    videoInput: str = Field(min_length=1)
    style: str | None = None
    resolution: Literal['480p', '720p', '1080p']

class SpeakerConfig(Model):
    appId: str
    cluster: str
    voiceType: str

class FontConfig(Model):
    color: str | None = None
    size: float | None = None
    name: str | None = None

class DramaRecapRequest(WorkflowBase):
    vids: tuple[str, ...] = Field(min_length=1, max_length=30)
    dramaScriptTaskId: str | None = None
    recapText: str | None = None
    speakerConfig: SpeakerConfig | None = None
    isEraseSubtitle: bool = True
    fontConfig: FontConfig | None = None
    recapStyle: str | None = None
    recapTextSpeed: float = Field(1.2, ge=0.5, le=2.0)
    recapTextLength: int | None = Field(None, ge=1, le=5000)
    pauseTime: int = Field(120, ge=1, le=1000)
    allowRepeatMatch: bool = False

WorkflowRequest = StyleTransferRequest | DramaRecapRequest

class AideoJob(Model):
    id: str
    persona: str
    state: Literal['running', 'succeeded', 'failed', 'unknown'] = 'running'
    channel: Literal['volcengine', 'style-transfer'] = 'volcengine'
    request_model: Literal['aideo', 'video-style-transfer', 'drama-recap'] = 'aideo'
    operation: Literal['aideo'] = 'aideo'
    provider_id: str = ''
    script_id: str = ''
    space: str
    request: AideoRequest | WorkflowRequest
    response: dict[str, JsonValue] = Field(default_factory=dict)
    assets: tuple[GeneratedAsset, ...] = ()
    error: str = ''
    created_at: float
    updated_at: float
