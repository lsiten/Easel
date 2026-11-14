"""Replay-safe DirectEdit submission and owned local result reconciliation."""
from collections.abc import Mapping
from pathlib import Path
import httpx
from pydantic import JsonValue, ValidationError
from easel.generation_aideo_media import archive
from easel.generation_aideo_provider import VodCall, VodConfig, mapping, string
from easel.generation_direct_edit_models import DirectEditJob, DirectEditRequest
from easel.generation_direct_edit_provider import DirectEditProvider
from easel.generation_direct_edit_store import DirectEditStore
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.ownership import OwnershipError
from easel.remote_media import RemoteMediaError


class DirectEditGeneration:
    def __init__(self, outputs: Path, settings: Mapping[str, str]) -> None:
        self.store = DirectEditStore(GenerationStore(outputs))
        self.settings = settings

    async def submit(self, request: DirectEditRequest) -> DirectEditJob:
        existing = self.store.replay(request)
        if existing is not None:
            return existing
        provider = DirectEditProvider(VodConfig.from_settings(self.settings))
        job, fresh = self.store.claim(request, request.SpaceName or provider.config.space)
        if not fresh:
            return job
        submitted = False
        try:
            payload = await provider.payload(job)
            submitted = True
            response = await provider.direct_call(VodCall('SubmitDirectEditTaskAsync', payload,
                                                          method='POST', version='2018-01-01'))
            identifier = string(mapping(response).get('ReqId'))
            if not identifier:
                raise GenerationError('direct_edit_provider_id_missing', 502)
            return self.store.save(job.model_copy(update={'provider_id': identifier, 'state': 'running', 'error': ''}))
        except (httpx.HTTPError, GenerationError, ValidationError, OSError) as error:
            ambiguous = submitted and not (isinstance(error, GenerationError) and error.code == 'direct_edit_provider_rejected')
            return self.store.save(job.model_copy(update={'state': 'unknown' if ambiguous else 'failed',
                'error': 'direct_edit_submission_unknown' if ambiguous else 'direct_edit_submission_failed'}))

    async def get(self, persona: str, identifier: str) -> DirectEditJob:
        with self.store.poll(persona, identifier) as (job, owner):
            if not owner:
                return job
            return await self._reconcile(job, owner)

    async def _reconcile(self, job: DirectEditJob, owner: str) -> DirectEditJob:
        provider = DirectEditProvider(VodConfig.from_settings(self.settings))
        try:
            response = await provider.direct_call(VodCall('GetDirectEditResult', {'ReqIds': [job.provider_id]},
                                                          method='POST', version='2018-01-01'))
            receipt = provider.receipt(response, job.provider_id)
            safe = provider.sanitized(receipt)
            job = self.store.save(job.model_copy(update={'response': safe, 'error': ''}), owner)
            status = receipt.Status.lower()
            if status in ('pending', 'start', 'processing'):
                return job
            if status in ('failed', 'failed_run', 'user_canceled'):
                return self.store.save(job.model_copy(update={'state': 'failed', 'error': 'direct_edit_provider_task_failed'}), owner)
            if status not in ('success', 'completed'):
                raise GenerationError('direct_edit_provider_status_invalid', 502)
            if not receipt.OutputVid and not receipt.OutputUrl:
                raise GenerationError('direct_edit_output_missing', 502)
            video: dict[str, JsonValue] = {'Vid': receipt.OutputVid} if receipt.OutputVid else {'Url': receipt.OutputUrl}
            assets = await archive(self.store.base.outputs, job, [video], provider)
            safe = safe.model_copy(update={'OutputUrl': string(video['url'])})
            return self.store.save(job.model_copy(update={'state': 'succeeded', 'response': safe, 'assets': assets}), owner)
        except (httpx.HTTPError, GenerationError, ValidationError, OSError, OwnershipError, RemoteMediaError):
            return self.store.save(job.model_copy(update={'error': 'direct_edit_reconciliation_pending'}), owner)


def public_job(job: DirectEditJob) -> dict[str, JsonValue]:
    return {'taskId': job.id, 'model': job.request_model, 'state': job.state,
            'status': {'running': 'Processing', 'unknown': 'Processing', 'succeeded': 'Completed', 'failed': 'Failed'}[job.state],
            'outputUrl': job.response.OutputUrl if job.response else None,
            'outputVid': job.response.OutputVid if job.response else None,
            'errorMessage': job.error or None, 'message': job.response.Message if job.response else None,
            'createdAt': job.created_at, 'updatedAt': job.updated_at,
            'assets': [asset.model_dump(mode='json') for asset in job.assets]}
