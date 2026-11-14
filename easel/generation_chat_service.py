"""Native chat transport with durable, non-retrying paid-call boundaries."""
from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Mapping
import httpx
from pydantic import JsonValue, TypeAdapter
from easel.direct_api import _sse_payloads
from easel.generation_chat_config import ChatEndpoint, chat_models, endpoint
from easel.generation_chat_media import archive_images
from easel.generation_chat_inputs import ChatInputContext, prepare_chat_inputs
from easel.generation_chat_models import ChatRecord, ChatRequest, CompletionRequest
from easel.generation_chat_store import ChatStore
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore

_JSON = TypeAdapter(dict[str, JsonValue])


class ChatGeneration:
    def __init__(self, store: GenerationStore, settings: Mapping[str, str]) -> None:
        self.store = ChatStore(store)
        self.settings = settings

    def prepare(self, request: ChatRequest, operation: str) -> tuple[ChatEndpoint, dict[str, JsonValue], dict[str, str]]:
        models = chat_models(self.store.base.outputs, request.persona, self.settings)
        model = next((item for item in models if item.name == request.model), None)
        if model is None:
            raise GenerationError('chat_model_invalid', 400)
        if not model.configured:
            raise GenerationError('chat_provider_configuration_required', 409)
        claude = operation in {'claude', 'claude_completion'}
        target = endpoint(self.settings, model, claude)
        body = request.model_dump(mode='json', exclude={'persona', 'request_key'}, exclude_none=True)
        if claude and isinstance(request, CompletionRequest):
            system = '\n\n'.join(str(message.content) for message in request.messages
                                  if message.role == 'system')
            messages = []
            for message in request.messages:
                if message.role == 'system':
                    continue
                messages.append({'role': message.role, 'content': _claude_content(message.content)})
            body = {
                'model': target.model,
                'messages': messages,
                'max_tokens': int(request.maxTokens or request.maxCompletionTokens or 4096),
            }
            if system:
                body['system'] = system
            if request.temperature is not None:
                body['temperature'] = request.temperature
            if request.topP is not None:
                body['top_p'] = request.topP
        elif isinstance(request, CompletionRequest):
            body = {**request.modelKwargs, **body}
            body.pop('modelKwargs', None)
            for source, dest in (('maxTokens', 'max_tokens'), ('maxCompletionTokens', 'max_completion_tokens'), ('topP', 'top_p')):
                if source in body:
                    body[dest] = body.pop(source)
        body['model'] = target.model
        body['stream'] = operation not in {'completion', 'claude_completion'}
        if operation == 'stream':
            body['stream_options'] = {'include_usage': True}
        headers = {'x-api-key': target.key, 'anthropic-version': '2023-06-01'} if claude else (
            {'Authorization': 'Bearer ' + target.key} if target.key else {})
        return target, body, headers

    async def completion(self, request: CompletionRequest, *, origin: str = '') -> tuple[ChatRecord, dict[str, JsonValue]]:
        models = chat_models(self.store.base.outputs, request.persona, self.settings)
        configured = next((item for item in models if item.name == request.model), None)
        operation = 'claude_completion' if configured and configured.channel == 'anthropic' else 'completion'
        existing = self.store.replay(request, operation)
        if existing is not None:
            if existing.state == 'succeeded' and existing.response is not None:
                return existing, existing.response
            raise GenerationError('chat_request_' + existing.state, 409)
        target, body, headers = self.prepare(request, operation)
        inputs = prepare_chat_inputs(body, ChatInputContext(self.store.base, request.persona, origin,
                                    self.settings.get('EASEL_CHANNEL_MEDIA_BASE_URL', '')))
        record, fresh = self.store.claim(request, target.channel, operation)
        if not fresh:
            if record.state == 'succeeded' and record.response is not None:
                return record, record.response
            raise GenerationError('chat_request_' + record.state, 409)
        try:
            body = inputs.bind(record)
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=300) as client:
                response = await client.post(target.url, json=body, headers=headers)
                if response.status_code != 200:
                    raise GenerationError('chat_provider_http_' + str(response.status_code), 502)
                data = _JSON.validate_json(response.content)
            if operation == 'claude_completion':
                usage = data.get('usage')
                content = data.get('content')
                if not isinstance(usage, dict) or not isinstance(content, list):
                    raise GenerationError('chat_response_invalid', 502)
                text = ''.join(str(block.get('text', '')) for block in content
                               if isinstance(block, dict) and block.get('type') == 'text')
                result = {'content': text, 'model': request.model, 'usage': normalize_claude_usage(usage)}
            else:
                usage = data.get('usage')
                choices = data.get('choices')
                if not isinstance(usage, dict) or not isinstance(choices, list) or not choices:
                    raise GenerationError('chat_response_invalid', 502)
                choice = choices[0]
                if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
                    raise GenerationError('chat_response_invalid', 502)
                message = choice['message']
                result = {'content': archive_images(message.get('content') or '', record, self.store.base),
                          'model': request.model, 'usage': normalize_usage(usage)}
                for field in ('tool_calls', 'function_call', 'audio', 'refusal'):
                    if field in message:
                        result[field] = message[field]
            self.store.finish(record.model_copy(update={'state': 'succeeded', 'response': result}))
            return record, result
        except (httpx.HTTPError, ValueError, GenerationError) as error:
            code = error.code if isinstance(error, GenerationError) else 'chat_provider_interrupted'
            self.store.finish(record.model_copy(update={'state': 'unknown', 'error': code}))
            raise GenerationError(code, 502) from None
        finally:
            # A cancelled coroutine must leave an ambiguous, non-replayable claim.
            with self.store.base.connect() as db:
                row = db.execute('SELECT state FROM chat_jobs WHERE id=?', (record.id,)).fetchone()
            if row['state'] == 'running':
                self.store.finish(record.model_copy(update={'state': 'unknown', 'error': 'chat_provider_interrupted'}))

    async def replay_events(self, record: ChatRecord, after: int) -> AsyncIterator[str]:
        """Serve persisted frames independently of model configuration and input files."""
        for event in self.store.events(record, after):
            yield event

    async def stream(self, request: ChatRequest, claude: bool = False, after: int = 0, *, origin: str = '') -> tuple[ChatRecord, AsyncIterator[str]]:
        operation = 'claude' if claude else 'stream'
        existing = self.store.replay(request, operation)
        if existing is not None:
            if existing.state != 'succeeded':
                raise GenerationError('chat_request_' + existing.state, 409)
            return existing, self.replay_events(existing, after)
        if after:
            raise GenerationError('chat_resume_invalid', 409)
        target, body, headers = self.prepare(request, operation)
        inputs = prepare_chat_inputs(body, ChatInputContext(self.store.base, request.persona, origin,
                                    self.settings.get('EASEL_CHANNEL_MEDIA_BASE_URL', '')))
        record, fresh = self.store.claim(request, target.channel, operation)
        if not fresh:
            if record.state != 'succeeded':
                raise GenerationError('chat_request_' + record.state, 409)
            return record, self.replay_events(record, after)

        async def events() -> AsyncIterator[str]:
            complete = False
            usage: dict[str, JsonValue] = {}
            try:
                prepared_body = inputs.bind(record)
                async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=300) as client:
                    async with client.stream('POST', target.url, json=prepared_body, headers=headers) as response:
                        if response.status_code != 200:
                            raise GenerationError('chat_provider_http_' + str(response.status_code), 502)
                        async for payload in _sse_payloads(response):
                            if payload == '[DONE]':
                                complete = True
                                break
                            data = _JSON.validate_json(payload)
                            if data.get('error') or data.get('type') == 'error':
                                raise GenerationError('chat_provider_stream_error', 502)
                            event_usage = data.get('usage')
                            message = data.get('message')
                            if isinstance(message, dict) and isinstance(message.get('usage'), dict):
                                usage.update(message['usage'])
                            if isinstance(event_usage, dict):
                                usage.update(event_usage)
                            if data.get('type') == 'message_stop':
                                complete = True
                            processed = archive_images(data, record, self.store.base)
                            event = ('event: ' + str(data['type']) + '\n' if claude and 'type' in data else '')
                            event += 'data: ' + json.dumps(processed, ensure_ascii=False) + '\n\n'
                            sequence = self.store.append(record.id, event)
                            yield f'id: {sequence}\n' + event
                if not complete:
                    raise GenerationError('chat_stream_incomplete', 502)
                if not claude:
                    event = 'data: [DONE]\n\n'
                    sequence = self.store.append(record.id, event)
                    yield f'id: {sequence}\n' + event
                summary = {'model': request.model, 'usage': normalize_claude_usage(usage) if claude else normalize_usage(usage)}
                self.store.finish(record.model_copy(update={'state': 'succeeded', 'response': summary}))
            except (httpx.HTTPError, ValueError, GenerationError) as error:
                code = error.code if isinstance(error, GenerationError) else 'chat_provider_interrupted'
                self.store.finish(record.model_copy(update={'state': 'unknown', 'error': code}))
                yield 'event: error\ndata: ' + json.dumps({'error': {'code': code}}) + '\n\n'
            finally:
                with self.store.base.connect() as db:
                    row = db.execute('SELECT state FROM chat_jobs WHERE id=?', (record.id,)).fetchone()
                if row['state'] == 'running':
                    self.store.finish(record.model_copy(update={'state': 'unknown', 'error': 'chat_provider_interrupted'}))
        return record, events()


def normalize_usage(usage: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result = {}
    for source, target in (('prompt_tokens', 'input_tokens'), ('completion_tokens', 'output_tokens'), ('total_tokens', 'total_tokens'),
                           ('prompt_tokens_details', 'input_token_details'), ('completion_tokens_details', 'output_token_details')):
        if source in usage:
            result[target] = usage[source]
    cached = usage.get('prompt_cache_hit_tokens')
    details = usage.get('prompt_tokens_details')
    if cached is None and isinstance(details, dict):
        cached = details.get('cached_tokens')
    if isinstance(cached, (int, float)):
        result['input_token_details'] = {**(details if isinstance(details, dict) else {}), 'cache_read': cached}
        tokens = usage.get('prompt_tokens')
        if isinstance(tokens, (int, float)):
            result['input_tokens'] = usage.get('prompt_cache_miss_tokens', max(tokens - cached, 0))
    return result


def _claude_content(content: JsonValue) -> JsonValue:
    """Translate the OpenAI-style content used by internal callers to Claude."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    result: list[JsonValue] = []
    for part in content:
        if not isinstance(part, dict):
            result.append({'type': 'text', 'text': json.dumps(part, ensure_ascii=False)})
            continue
        if part.get('type') == 'text':
            result.append({'type': 'text', 'text': str(part.get('text', ''))})
            continue
        if part.get('type') == 'image_url':
            image = part.get('image_url')
            url = image.get('url', '') if isinstance(image, dict) else ''
            if not isinstance(url, str) or not url:
                raise GenerationError('chat_image_input_invalid', 400)
            if url.startswith('data:'):
                header, _, encoded = url.partition(',')
                mime = header[5:].split(';', 1)[0] or 'image/png'
                try:
                    base64.b64decode(encoded, validate=True)
                except ValueError:
                    raise GenerationError('chat_image_input_invalid', 400) from None
                result.append({'type': 'image', 'source': {
                    'type': 'base64', 'media_type': mime, 'data': encoded}})
            else:
                result.append({'type': 'image', 'source': {'type': 'url', 'url': url}})
            continue
        result.append({'type': 'text', 'text': json.dumps(part, ensure_ascii=False)})
    return result


def normalize_claude_usage(usage: dict[str, JsonValue]) -> dict[str, JsonValue]:
    result = {key: usage[key] for key in ('input_tokens', 'output_tokens') if key in usage}
    details = {}
    for source, target in (('cache_read_input_tokens', 'cache_read'), ('cache_creation_input_tokens', 'cache_creation_5m')):
        if source in usage:
            details[target] = usage[source]
    cache = usage.get('cache_creation')
    if isinstance(cache, dict):
        for source, target in (('ephemeral_5m_input_tokens', 'cache_creation_5m'), ('ephemeral_1h_input_tokens', 'cache_creation_1h')):
            if source in cache:
                details[target] = cache[source]
    if details:
        result['input_token_details'] = details
    return result
