"""Replay-safe Aideo submission and restart reconciliation through native IDs."""
from collections.abc import Mapping
from pathlib import Path
import json
import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError
from easel.generation_aideo_media import archive, input_vids
from easel.generation_aideo_models import AideoJob, AideoRequest, PromptRequest
from easel.generation_aideo_provider import VodCall, VodConfig, VodProvider, string
from easel.generation_aideo_results import interpret
from easel.generation_aideo_store import AideoStore
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.ownership import OwnershipError
from easel.remote_media import RemoteMediaError

class AideoGeneration:
    def __init__(self, outputs: Path, settings: Mapping[str, str]) -> None:
        self.store = AideoStore(GenerationStore(outputs))
        self.settings = settings

    async def submit(self, request: AideoRequest) -> AideoJob:
        previous = self.store.replay(request)
        if previous is not None:
            return previous
        provider = VodProvider(VodConfig.from_settings(self.settings))
        space = request.spaceName or provider.config.space
        job, fresh = self.store.claim(request, space)
        if not fresh:
            return job
        try:
            vids = await input_vids(self.store.base.outputs, job, provider)
            payload: dict[str, JsonValue] = {'SpaceName': space, 'MultiInputs': vids}
            if isinstance(request, PromptRequest):
                payload['Prompt'] = request.prompt
            else:
                payload['SkillType'] = request.skillType
                if request.skillParams is not None:
                    payload['SkillParams'] = json.dumps(request.skillParams, ensure_ascii=False, separators=(',', ':'))
            result = await provider.call(VodCall('SubmitAideoTaskAsync', payload, method='POST', version='2025-03-03'))
            identifier = string(result.get('TaskId'))
            if not identifier:
                raise GenerationError('aideo_provider_task_id_missing', 502)
            return self.store.save(job.model_copy(update={'provider_id': identifier, 'state': 'running', 'error': ''}))
        except (httpx.HTTPError, GenerationError, ValidationError, OSError, OwnershipError, RemoteMediaError) as error:
            ambiguous = isinstance(error, (httpx.HTTPError, ValidationError)) or isinstance(error, GenerationError) and error.code in ('aideo_provider_response_invalid', 'aideo_provider_task_id_missing')
            code = 'aideo_submission_unknown' if ambiguous else 'aideo_submission_failed'
            return self.store.save(job.model_copy(update={'state': 'unknown' if ambiguous else 'failed', 'error': code}))

    async def get(self, persona: str, identifier: str) -> AideoJob:
        job = self.store.get(persona, identifier)
        if job.request_model != 'aideo':
            from easel.generation_aideo_workflows import AideoWorkflows
            return await AideoWorkflows(self).get(persona, identifier)
        with self.store.poll(persona, identifier) as claim:
            if not claim.owner:
                return claim.job
            return await self._reconcile(claim.job, claim.owner)

    async def _reconcile(self, job: AideoJob, owner: str) -> AideoJob:
        try:
            provider = VodProvider(VodConfig.from_settings(self.settings))
            response = await provider.call(VodCall('GetAideoTaskResult', {'TaskId': job.provider_id, 'SpaceName': job.space}, version='2025-03-03'))
            encoded = json.dumps(response, ensure_ascii=False)
            for secret in (provider.config.secret_key, provider.config.access_key, provider.config.playback_key):
                if secret:
                    encoded = encoded.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[redacted]')
            response = TypeAdapter(dict[str, JsonValue]).validate_json(encoded)
            if response.get('TaskId') and response['TaskId'] != job.provider_id:
                raise GenerationError('aideo_provider_identity_mismatch', 502)
            result = interpret(response)
            job = self.store.save(job.model_copy(update={'response': response, 'error': result.error}), owner)
            assets = await archive(self.store.base.outputs, job, result.videos, provider) if result.state == 'succeeded' else ()
            return self.store.save(job.model_copy(update={'state': result.state, 'response': response, 'assets': assets}), owner)
        except (httpx.HTTPError, GenerationError, ValidationError, OSError, OwnershipError, RemoteMediaError):
            return self.store.save(job.model_copy(update={'error': 'aideo_reconciliation_pending'}), owner)


def public_job(job: AideoJob) -> dict[str, JsonValue]:
    if job.request_model != 'aideo':
        from easel.generation_aideo_workflows import public_workflow
        return public_workflow(job)
    state = {'running': 'Processing', 'unknown': 'Processing', 'succeeded': 'Completed', 'failed': 'Failed'}[job.state]
    return {'taskId': job.id, 'model': job.request_model, 'status': state, 'state': job.state,
            'skillType': job.response.get('SkillType'),
            'skillParams': json.dumps(job.response['SkillParams']) if job.response.get('SkillParams') else None,
            'apiResponses': job.response.get('ApiResponses'), 'errorMessage': job.error or None,
            'createdAt': job.created_at, 'updatedAt': job.updated_at,
            'assets': [asset.model_dump(mode='json') for asset in job.assets]}
