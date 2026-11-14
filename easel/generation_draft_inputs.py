"""Normalize only this service's image references through the owned image boundary."""
from easel.generation_draft_models import ImageDraftInput, VideoDraftInput
from easel.generation_inputs import local_image_request
from easel.generation_models import ImageRequest


def normalized_draft_images(payload: ImageDraftInput | VideoDraftInput, origin: str) -> ImageDraftInput | VideoDraftInput:
    if not payload.imageUrls:
        return payload
    request = local_image_request(ImageRequest(persona='draft', request_key='normalization',
        model='normalization', prompt='normalization', operation='edit',
        image=tuple(str(url) for url in payload.imageUrls)), origin)
    return payload.model_copy(update={'imageUrls': request.images})
