"""DirectEdit native array envelopes and source payload construction."""
from datetime import datetime, timezone
import httpx
from pydantic import JsonValue, TypeAdapter
from easel.generation_aideo_provider import VodCall, VodProvider, mapping, sequence, signed_request
from easel.generation_direct_edit_models import CanvasParam, DirectEditJob, DirectEditReceipt, UploadParam
from easel.generation_models import GenerationError


class DirectEditProvider(VodProvider):
    async def direct_call(self, call: VodCall) -> JsonValue:
        request = signed_request(self.config, call, datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=120) as client:
            response = await client.send(request)
        response.raise_for_status()
        value = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        if mapping(value.get('ResponseMetadata', {})).get('Error'):
            raise GenerationError('direct_edit_provider_rejected', 502)
        return value.get('Result')

    async def payload(self, job: DirectEditJob) -> dict[str, JsonValue]:
        request = job.request
        edit = request.edit()
        canvas = edit.Canvas
        if canvas is None:
            vid = next((element.Source[6:] for layer in edit.Track for element in layer
                        if element.Type == 'video' and element.Source and element.Source.startswith('vid://')), '')
            if not vid:
                raise GenerationError('direct_edit_canvas_required', 422)
            response = await self.call(VodCall('GetMediaInfos', {'Vids': vid}))
            entries = sequence(response.get('MediaInfoList'))
            if not entries:
                raise GenerationError('direct_edit_canvas_unavailable', 502)
            source = mapping(mapping(entries[0]).get('SourceInfo'))
            canvas = CanvasParam.model_validate({key: source.get(key) for key in ('Width', 'Height')})
        upload = edit.Upload or UploadParam(SpaceName=job.space,
            VideoName='direct_edit_' + job.id, FileName='direct_edit_' + job.id + '.mp4')
        param = edit.model_copy(update={'Canvas': canvas, 'Upload': upload})
        payload: dict[str, JsonValue] = {'Uploader': request.Uploader or job.space,
                                       'Application': request.Application,
                                       'EditParam': param.model_dump(mode='json', exclude_none=True)}
        if request.Priority is not None:
            payload['Priority'] = request.Priority
        if request.CallbackUrl:
            payload['CallbackUrl'] = request.CallbackUrl
        if request.CallbackArgs:
            payload['CallbackArgs'] = request.CallbackArgs
        return payload

    def receipt(self, value: JsonValue, identifier: str) -> DirectEditReceipt:
        if isinstance(value, list):
            value = value[0] if value else {'ReqId': identifier, 'Status': 'Processing'}
        raw = mapping(value)
        allowed = {key: raw[key] for key in DirectEditReceipt.model_fields if key in raw and raw[key] is not None}
        receipt = DirectEditReceipt.model_validate(allowed)
        if receipt.ReqId and receipt.ReqId != identifier:
            raise GenerationError('direct_edit_provider_identity_mismatch', 502)
        # TaskId can identify an internal VOD subtask; ReqId is the submit receipt identity.
        return receipt

    def sanitized(self, receipt: DirectEditReceipt) -> DirectEditReceipt:
        """Persist only native status identifiers; remote URLs are ephemeral secrets."""
        values = receipt.model_dump()
        values['OutputUrl'] = ''
        for key, value in values.items():
            for secret in (self.config.access_key, self.config.secret_key, self.config.playback_key):
                if secret:
                    value = value.replace(secret, '[redacted]')
            values[key] = value
        return DirectEditReceipt.model_validate(values)
