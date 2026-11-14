"""Native style-transfer and recap business payloads from the source services."""
from pydantic import JsonValue, TypeAdapter
from easel.generation_aideo_models import AideoJob, DramaRecapRequest, StyleTransferRequest
from easel.generation_aideo_provider import VodCall, string
from easel.generation_aideo_results import Result
from easel.generation_models import GenerationError


def submit_call(job: AideoJob, vids: list[str]) -> VodCall:
    request = job.request
    match request:
        case StyleTransferRequest():
            parameters: dict[str, JsonValue] = {'input': 'vid://' + vids[0], 'space_name': job.space, 'resolution': request.resolution}
            if request.style is not None:
                parameters['style'] = request.style
            return VodCall('AsyncVCreativeTask', {'Scene': 'videostyletrans', 'Uploader': job.space,
                'ParamObj': parameters}, method='POST', version='2018-01-01')
        case DramaRecapRequest():
            recap: dict[str, JsonValue] = {'RecapTextSpeed': request.recapTextSpeed, 'PauseTime': request.pauseTime,
                                         'AllowRepeatMatch': request.allowRepeatMatch}
            if request.recapText:
                recap['RecapText'] = request.recapText
            else:
                recap['AutoGenerateRecapText'] = True
            if request.recapStyle:
                recap['RecapStyle'] = request.recapStyle
            if request.recapTextLength is not None:
                recap['RecapTextLength'] = request.recapTextLength
            payload: dict[str, JsonValue] = {'SpaceName': job.space, 'Vids': list(vids), 'DramaRecapConfig': recap,
                                             'IsEraseSubtitle': request.isEraseSubtitle}
            if request.dramaScriptTaskId:
                payload['DramaScriptTaskId'] = request.dramaScriptTaskId
            if request.speakerConfig:
                speaker = request.speakerConfig
                payload['SpeakerConfig'] = {'AppId': speaker.appId, 'Cluster': speaker.cluster, 'VoiceType': speaker.voiceType}
            if request.fontConfig:
                payload['FontConfig'] = {name[0].upper() + name[1:]: value for name, value in
                    request.fontConfig.model_dump(mode='json', exclude_none=True).items()}
            return VodCall('CreateDramaRecapTask', payload, method='POST', version='2025-03-03')
        case _:
            raise GenerationError('aideo_workflow_invalid', 409)


def status_call(job: AideoJob) -> VodCall:
    if job.request_model == 'video-style-transfer':
        return VodCall('GetVCreativeTaskResult', {'VCreativeId': job.provider_id}, version='2018-01-01')
    return VodCall('QueryDramaRecapTask', {'TaskId': job.provider_id, 'SpaceName': job.space}, version='2025-03-03')


def interpret_workflow(job: AideoJob, response: dict[str, JsonValue]) -> Result:
    expected = 'VCreativeId' if job.request_model == 'video-style-transfer' else 'TaskId'
    if response.get(expected) and response[expected] != job.provider_id:
        raise GenerationError('aideo_provider_identity_mismatch', 502)
    status = response.get('Status')
    if status in ('Processing', 'processing'):
        return Result('running', [])
    if status in ('Failed', 'failed_run'):
        return Result('failed', [], 'aideo_workflow_task_failed')
    if job.request_model == 'video-style-transfer' and status == 'success':
        output = TypeAdapter(dict[str, JsonValue]).validate_json(string(response.get('OutputJson')))
        vid = string(output.get('vid'))
    elif job.request_model == 'drama-recap' and status == 'Completed':
        vid = string(response.get('Vid'))
    else:
        raise GenerationError('aideo_provider_status_invalid', 502)
    if not vid:
        raise GenerationError('aideo_result_empty', 502)
    return Result('succeeded', [{'Vid': vid}])
