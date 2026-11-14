"""Durable business workflows independent of the source MCP protocol product."""
from typing import TYPE_CHECKING
import httpx
from pydantic import JsonValue, ValidationError
from easel.generation_aideo_media import archive, input_vids
from easel.generation_aideo_models import AideoJob, DramaRecapRequest, PromptRequest, StyleTransferRequest, WorkflowRequest
from easel.generation_aideo_provider import VodConfig, VodProvider, mapping, string
from easel.generation_aideo_workflow_payloads import interpret_workflow, status_call, submit_call
from easel.generation_models import GenerationError
from easel.ownership import OwnershipError
from easel.remote_media import RemoteMediaError
if TYPE_CHECKING:
    from easel.generation_aideo_service import AideoGeneration


class AideoWorkflows:
    def __init__(self, generation: 'AideoGeneration') -> None:
        self.store = generation.store
        self.settings = generation.settings

    async def submit(self, request: WorkflowRequest) -> AideoJob:
        previous = self.store.replay(request)
        if previous is not None:
            return previous
        provider = VodProvider(VodConfig.from_settings(self.settings))
        job, fresh = self.store.claim(request, provider.config.space)
        if not fresh:
            return job
        try:
            inputs = (request.videoInput,) if isinstance(request, StyleTransferRequest) else request.vids
            # Plain provider VIDs retain the source service convention; paths stay owned inputs.
            references = tuple('vid://' + value if '/' not in value and '.' not in value else value for value in inputs)
            prepared = job.model_copy(update={'request': PromptRequest(persona=request.persona, request_key=request.request_key,
                prompt='workflow media preparation', multiInputs=references)})
            uploaded = await input_vids(self.store.base.outputs, prepared, provider)
            vids = [string(mapping(entry).get('Vid')) for entry in uploaded]
            result = await provider.call(submit_call(job, vids))
            field = 'VCreativeId' if isinstance(request, StyleTransferRequest) else 'TaskId'
            identifier = string(result.get(field))
            if not identifier:
                raise GenerationError('aideo_provider_task_id_missing', 502)
            script = result.get('DramaScriptTaskId')
            script_id = script if isinstance(script, str) else request.dramaScriptTaskId or '' if isinstance(request, DramaRecapRequest) else ''
            # Both receipts are committed atomically before returning or polling.
            job = self.store.save(job.model_copy(update={'provider_id': identifier, 'script_id': script_id,
                'state': 'running', 'error': ''}))
            return job
        except (httpx.HTTPError, GenerationError, ValidationError, OSError, OwnershipError, RemoteMediaError) as error:
            ambiguous = isinstance(error, (httpx.HTTPError, ValidationError)) or isinstance(error, GenerationError) and error.code in ('aideo_provider_response_invalid', 'aideo_provider_task_id_missing')
            return self.store.save(job.model_copy(update={'state': 'unknown' if ambiguous else 'failed',
                'error': 'aideo_submission_unknown' if ambiguous else 'aideo_submission_failed'}))

    async def get(self, persona: str, identifier: str) -> AideoJob:
        job = self.store.get(persona, identifier)
        if job.request_model not in ('video-style-transfer', 'drama-recap'):
            raise GenerationError('aideo_workflow_not_found', 404)
        with self.store.poll(persona, identifier) as claim:
            if not claim.owner:
                return claim.job
            return await self._reconcile(claim.job, claim.owner)

    async def _reconcile(self, job: AideoJob, owner: str) -> AideoJob:
        try:
            provider = VodProvider(VodConfig.from_settings(self.settings))
            response = await provider.call(status_call(job))
            result = interpret_workflow(job, response)
            # Preserve only the operational fields needed by Easel, never provider error dumps.
            saved: dict[str, JsonValue] = {'Status': response.get('Status')}
            if isinstance(response.get('DramaScriptTaskId'), str):
                job = self.store.save(job.model_copy(update={'script_id': response['DramaScriptTaskId']}), owner)
            assets = await archive(self.store.base.outputs, job, result.videos, provider) if result.state == 'succeeded' else ()
            if result.videos:
                saved['outputVid'] = result.videos[0]['Vid']
                saved['outputUrl'] = result.videos[0]['url']
            return self.store.save(job.model_copy(update={'state': result.state, 'assets': assets,
                'response': saved, 'error': result.error}), owner)
        except (httpx.HTTPError, GenerationError, ValidationError, OSError, OwnershipError, RemoteMediaError):
            return self.store.save(job.model_copy(update={'error': 'aideo_reconciliation_pending'}), owner)


def public_workflow(job: AideoJob) -> dict[str, JsonValue]:
    return {'taskId': job.id, 'model': job.request_model, 'state': job.state,
            'status': 'Completed' if job.state == 'succeeded' else 'Failed' if job.state == 'failed' else 'Processing',
            'dramaScriptTaskId': job.script_id or None, 'outputVid': job.response.get('outputVid'),
            'outputUrl': job.response.get('outputUrl'), 'errorMessage': job.error or None,
            'createdAt': job.created_at, 'updatedAt': job.updated_at,
            'assets': [asset.model_dump(mode='json') for asset in job.assets]}
