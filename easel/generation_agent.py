"""Agent lifecycle service backed exclusively by the existing Easel chat runtime."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
import anyio
from typing_extensions import assert_never
from easel.generation_agent_models import (AgentHooks, AgentRun, AgentSnapshot, AgentTask, CreateTask,
    PublicMessage, PublicTask, TaskMessages)
from easel.generation_agent_store import AgentStore
from easel.generation_models import GenerationError


class AgentService:
    def __init__(self, store: AgentStore, hooks: AgentHooks) -> None:
        self.store = store
        self.hooks = hooks

    async def refresh(self, persona: str, task_id: str) -> AgentTask:
        task = self.store.get(persona, task_id)
        if task.status == 'running':
            run = self.store.run(persona, task_id)
            self.store.ingest(run, await self.hooks.poll(run))
            task = self.store.get(persona, task_id)
        return task

    async def create(self, persona: str, payload: CreateTask) -> AgentRun:
        if payload.taskId:
            await self.refresh(persona, payload.taskId)
        run = self.store.claim(persona, payload)
        with anyio.CancelScope(shield=True):
            try:
                await self.hooks.start(run)
            except Exception:  # noqa: BROAD_EXCEPT_OK -- HTTP launch boundary must release failed claims.
                logging.getLogger(__name__).exception('Agent task launch failed')
                self.store.ingest(run, AgentSnapshot(status='error'))
                raise
            try:
                current = self.store.get(persona, run.task_id)
            except GenerationError:
                await self.hooks.stop(run)
                raise
            if current.status == 'aborted':
                await self.hooks.stop(run)
        return run

    async def messages(self, persona: str, task_id: str, last: str = '') -> TaskMessages:
        task = await self.refresh(persona, task_id)
        messages = task.messages
        if last:
            position = next((i for i, message in enumerate(messages) if message.uuid == last), None)
            messages = messages[position + 1:] if position is not None else ()
        return TaskMessages(messages=messages, status=task.status)

    async def stream(self, run: AgentRun, partial: bool) -> AsyncIterator[str]:
        import json
        yield 'data: ' + json.dumps({'type': 'init', 'taskId': run.task_id}) + '\n\n'
        last = ''
        while True:
            result = await self.messages(run.persona, run.task_id, last)
            for event in result.messages:
                last = event.uuid
                if partial or event.type not in ('token', 'thinking', 'stream_event'):
                    yield 'id: ' + event.uuid + '\ndata: ' + event.model_dump_json() + '\n\n'
            if result.status != 'running':
                yield 'data: ' + json.dumps({'type': 'complete', 'taskId': run.task_id, 'status': result.status}) + '\n\n'
                return
            yield 'data: {"type":"keep_alive"}\n\n'
            await anyio.sleep(0.25)

    async def abort(self, persona: str, task_id: str) -> None:
        task = await self.refresh(persona, task_id)
        if task.status != 'running':
            raise GenerationError('agent_task_not_running', 409)
        run = self.store.run(persona, task_id)
        with anyio.CancelScope(shield=True):
            await self.hooks.stop(run)
            self.store.ingest(run, AgentSnapshot(status='aborted'))

    async def delete(self, persona: str, task_id: str) -> None:
        run = self.store.run(persona, task_id)
        with anyio.CancelScope(shield=True):
            current_run = self.store.delete(run)
            await self.hooks.stop(current_run)
            await self.hooks.delete(current_run)

    def public(self, token: str) -> PublicTask:
        """Only explicit conversational text is public; never tool/runtime metadata."""
        task = self.store.shared(token)
        messages: list[PublicMessage] = []
        for event in task.messages:
            if event.type not in ('user', 'assistant'):
                continue
            match event.data:
                case str(text):
                    messages.append(PublicMessage(type='user' if event.type == 'user' else 'assistant', text=text))
                case list(parts):
                    text = '\n'.join(part['text'] for part in parts if isinstance(part, dict) and
                                     part.get('type') == 'text' and isinstance(part.get('text'), str))
                    if text:
                        messages.append(PublicMessage(type='user' if event.type == 'user' else 'assistant', text=text))
                case bool() | int() | float() | dict() | None:
                    continue
                case unreachable:
                    assert_never(unreachable)
        return PublicTask(id=task.id, title=task.title, status=task.status, messages=tuple(messages))
