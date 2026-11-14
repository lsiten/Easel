"""Google native image transport with durable provider receipts and bounded artifacts."""
import base64
import binascii
import io
import time
from pathlib import Path
from collections.abc import Mapping
from urllib.parse import quote
import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from easel.generation_models import GenerationError, GenerationJob, ImageReceipt, ImageUsage
from easel.harnesses.store import write_json


class NativeResponse(BaseModel):
    model_config = ConfigDict(extra='ignore')
    candidates: tuple['Candidate', ...] = ()
    usageMetadata: dict[str, JsonValue] = Field(default_factory=dict)
    modelVersion: str = ''


class InlineImage(BaseModel):
    mimeType: str = 'image/png'
    data: str


class NativePart(BaseModel):
    model_config = ConfigDict(extra='ignore')
    inlineData: InlineImage | None = None
    text: str = ''


class NativeContent(BaseModel):
    parts: tuple[NativePart, ...] = ()


class Candidate(BaseModel):
    model_config = ConfigDict(extra='ignore')
    content: NativeContent = Field(default_factory=NativeContent)


def native_ready(settings: Mapping[str, str]) -> bool:
    return bool(settings.get('GEMINI_API_KEY', '').strip())


def token_details(value: JsonValue) -> dict[str, float]:
    result = {}
    match value:
        case list() as entries:
            for entry in entries:
                match entry:
                    case {'modality': str(modality), 'tokenCount': int(count) | float(count)}:
                        if count >= 0:
                            result[modality.lower()] = float(count)
                    case _:
                        continue
        case _:
            return result
    return result


async def generate_native(job: GenerationJob, inputs: tuple[Path, ...], settings: Mapping[str, str], destination: Path, receipt_path: Path) -> None:
    request = job.request
    if request.mask or request.n != 1:
        raise GenerationError('generation_gemini_options_unsupported')
    if not native_ready(settings):
        raise GenerationError('generation_configuration_required', 409)
    base = settings.get('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta').rstrip('/')
    if not base.endswith(('/v1beta', '/v1')):
        base += '/v1beta'
    parts: list[dict[str, JsonValue]] = []
    for path in inputs:
        with Image.open(path) as picture:
            mime = Image.MIME.get(picture.format or '', 'image/png')
            picture.verify()
        parts.append({'inlineData': {'mimeType': mime, 'data': base64.b64encode(path.read_bytes()).decode()}})
    parts.append({'text': request.prompt})
    image_config = {key: value for key, value in (('imageSize', request.imageSize), ('aspectRatio', request.aspectRatio)) if value}
    model = job.runtime_model or request.model
    async with httpx.AsyncClient(timeout=850, follow_redirects=False, trust_env=False) as client:
        try:
            response = await client.post(base + '/models/' + quote(model, safe='') + ':generateContent',
                headers={'x-goog-api-key': settings['GEMINI_API_KEY']}, json={
                    'contents': [{'role': 'user', 'parts': parts}],
                    'generationConfig': {'responseModalities': ['IMAGE'], 'imageConfig': image_config}})
        except httpx.TransportError as error:
            # A lost response may already have incurred a charge; never automatically replay it.
            raise TimeoutError('native_provider_outcome_unknown') from error
    if response.status_code >= 500:
        raise TimeoutError('native_provider_outcome_unknown')
    if response.status_code >= 400:
        raise GenerationError('generation_provider_failed', 502)
    if len(response.content) > 100 * 1024**2:
        raise GenerationError('generation_artifact_invalid')
    payload = NativeResponse.model_validate_json(response.content)
    # Persist the received provider envelope before decoding; a local failure never resubmits.
    write_json(receipt_path.with_suffix('.native.json'), payload.model_dump(mode='json'))
    images = [part.inlineData for candidate in payload.candidates for part in candidate.content.parts if part.inlineData]
    if not images:
        raise GenerationError('generation_result_empty')
    for index, item in enumerate(images):
        try:
            data = base64.b64decode(item.data, validate=True)
        except binascii.Error as error:
            raise GenerationError('generation_artifact_invalid') from error
        if len(data) > 50 * 1024**2:
            raise GenerationError('generation_artifact_invalid')
        with Image.open(io.BytesIO(data)) as picture:
            picture.verify()
        with Image.open(io.BytesIO(data)) as picture:
            picture.save(destination / f'generated-{index}.png', format='PNG')
    usage = payload.usageMetadata
    receipt = ImageReceipt(created=time.time(), provider='gemini', model=payload.modelVersion or model,
        size=request.imageSize or request.size, output_format='png', usage=ImageUsage.model_validate({
            'input_tokens': usage.get('promptTokenCount'), 'output_tokens': usage.get('candidatesTokenCount'),
            'total_tokens': usage.get('totalTokenCount'),
            'inputTokenDetails': token_details(usage.get('promptTokensDetails')),
            'outputTokenDetails': token_details(usage.get('candidatesTokensDetails'))}))
    write_json(receipt_path, receipt.model_dump(mode='json'))
