"""Pinned Sora character extension over configured native video transport."""
from collections.abc import Mapping
from pathlib import Path

import anyio
import httpx
from pydantic import ValidationError

from easel._public_network import validate_public_url
from easel.generation_character_models import CharacterJob, CharacterReceipt, CharacterRequest
from easel.generation_character_store import CharacterStore
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.generation_video_provider import NativeVideoProvider
from easel.generation_video_store import VideoStore
from easel.remote_media import RemoteMediaError


class CharacterGeneration:
    def __init__(self, outputs: Path, settings: Mapping[str, str]) -> None:
        self.store = CharacterStore(GenerationStore(outputs))
        self.settings = settings

    async def submit(self, request: CharacterRequest) -> CharacterJob:
        existing = self.store.replay(request)
        if existing is not None:
            return existing
        provider = NativeVideoProvider(self.settings)
        values = {'model': 'sora-2-character', 'prompt': request.prompt, 'timestamps': request.timestamps}
        if request.taskId:
            video = VideoStore(self.store.base).get(request.persona, request.taskId, 'openai')
            if not video.provider_id:
                raise GenerationError('character_video_receipt_required', 409)
            values['taskId'] = video.provider_id
        elif request.videoUrl:
            try:
                values['url'] = await anyio.to_thread.run_sync(validate_public_url, str(request.videoUrl))
            except RemoteMediaError as error:
                raise GenerationError('character_source_url_invalid') from error
        job, fresh = self.store.claim(request)
        if not fresh:
            return job
        job = self.store.save(job.model_copy(update={'state': 'unknown', 'error': 'character_submission_in_progress_or_interrupted'}))
        try:
            async with provider.client() as client:
                response = await client.post(provider.base + '/videos', files={key: (None, value) for key, value in values.items()})
            response.raise_for_status()
            receipt = self.receipt(response, provider.key)
            return self.finish(job, receipt)
        except (httpx.HTTPError, ValidationError, GenerationError, UnicodeError):
            return self.store.save(job.model_copy(update={'error': 'character_submission_unknown'}))

    async def get(self, persona: str, identifier: str) -> CharacterJob:
        job = self.store.get(persona, identifier)
        if not job.provider_id or job.state in ('succeeded', 'failed'):
            return job
        try:
            provider = NativeVideoProvider(self.settings)
            async with provider.client() as client:
                response = await client.get(provider.base + '/videos/' + job.provider_id)
            response.raise_for_status()
            receipt = self.receipt(response, provider.key)
            if receipt.id != job.provider_id:
                raise GenerationError('character_provider_identity_mismatch', 502)
            return self.finish(job, receipt)
        except (httpx.HTTPError, ValidationError, GenerationError, UnicodeError):
            return self.store.save(job.model_copy(update={'error': 'character_reconciliation_pending'}))

    def finish(self, job: CharacterJob, receipt: CharacterReceipt) -> CharacterJob:
        state = 'succeeded' if receipt.status == 'completed' else 'failed' if receipt.status == 'failed' else 'running'
        return self.store.save(job.model_copy(update={'provider_id': receipt.id, 'response': receipt, 'state': state,
                                                    'error': 'character_provider_failed' if state == 'failed' else ''}))

    @staticmethod
    def receipt(response: httpx.Response, secret: str) -> CharacterReceipt:
        encoded = response.content.decode('utf-8')
        if secret:
            import json
            encoded = encoded.replace(json.dumps(secret)[1:-1], '[redacted]')
        return CharacterReceipt.model_validate_json(encoded)
