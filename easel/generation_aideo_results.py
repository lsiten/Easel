"""Source skill results with translation export and VCreative failure gates."""
import json
from dataclasses import dataclass
from typing import Literal
from pydantic import JsonValue, TypeAdapter
from easel.generation_aideo_provider import mapping, sequence
from easel.generation_models import GenerationError

@dataclass(frozen=True, slots=True)
class Result:
    state: Literal['running', 'succeeded', 'failed']
    videos: list[dict[str, JsonValue]]
    error: str = ''


def interpret(response: dict[str, JsonValue]) -> Result:
    status = response.get('Status')
    if status == 'Processing':
        return Result('running', [])
    if status == 'Failed':
        return Result('failed', [], 'aideo_provider_task_failed')
    if status != 'Completed':
        raise GenerationError('aideo_provider_status_invalid', 502)
    entries = sequence(response.get('ApiResponses', []))
    if not entries:
        return Result('failed', [], 'aideo_result_empty')
    videos: list[dict[str, JsonValue]] = []
    for entry in entries:
        api = mapping(entry)
        if api.get('Error'):
            return Result('failed', [], 'aideo_provider_task_failed')
        match api.get('VodTaskType'):
            case 'AITranslation':
                project = mapping(mapping(api.get('AITranslation', {})).get('ProjectInfo', {}))
                candidates = [mapping(project.get(key, {})) for key in ('OutputVideo', 'FacialTranslationVideo', 'VoiceTranslationVideo')]
                ready = [video for video in candidates if any(video.get(key) for key in ('Url', 'Vid', 'FileName'))]
                if not ready or project.get('Status') in ('InProcessing', 'ProcessSuspended', 'InExporting'):
                    return Result('running', [])
                videos.extend(ready)
            case 'Erase':
                file = mapping(mapping(mapping(mapping(mapping(api.get('Erase', {})).get('Output', {})).get('Task', {})).get('Erase', {})).get('File', {}))
                if not file:
                    return Result('failed', [], 'aideo_result_empty')
                videos.append(file)
            case 'Highlight':
                edits = sequence(mapping(api.get('Highlight', {})).get('Edits', []))
                if not edits:
                    return Result('failed', [], 'aideo_result_empty')
                videos.extend(mapping(edit) for edit in edits)
            case 'VCreative':
                creative = mapping(api.get('VCreative', {}))
                if creative.get('Status') != 'success':
                    return Result('failed', [], 'aideo_vcreative_failed')
                output = creative.get('OutputJson', {})
                if isinstance(output, str):
                    try:
                        output = TypeAdapter(dict[str, JsonValue]).validate_json(output)
                    except ValueError as error:
                        raise GenerationError('aideo_provider_response_invalid', 502) from error
                    creative['OutputJson'] = output
                output = mapping(output)
                target = mapping(output.get('Result', output))
                if target.get('vid'):
                    target['Vid'] = target['vid']
                if not target.get('Vid'):
                    return Result('failed', [], 'aideo_result_empty')
                videos.append(target)
            case 'Vision':
                if not api.get('Vision'):
                    return Result('failed', [], 'aideo_result_empty')
            case _:
                return Result('failed', [], 'aideo_result_skill_unknown')
    return Result('succeeded', videos)
