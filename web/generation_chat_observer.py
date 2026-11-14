"""Attach the existing chat producer to Agent history without launching another run."""
from __future__ import annotations

import uuid

from easel.generation_agent_models import AgentEvent, AgentRun, AgentSnapshot, CreateTask, TextBlock
from easel.generation_models import GenerationError
from easel.publish_recovery import worker_owner
from web.generation_agent_bridge import INTERNAL_AGENT_CHAT, service
from web.server_binding import server


def observe_chat(req: server.ChatRequest, runtime: str) -> AgentRun | None:
    """Called after chat validation, before starting its sole producer."""
    if INTERNAL_AGENT_CHAT.get() or not req.sessionId:
        return None
    persona = req.persona or ''
    agent = service()
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, 'easel:chat:' + persona + ':' + req.sessionId).hex
    existing = None
    try:
        existing = agent.store.get(persona, identifier)
    except GenerationError as error:
        if error.status != 404:
            raise
    prompt = (TextBlock(type='text', text=req.message or 'Attachments'),)
    run = AgentRun(persona=persona, task_id=identifier, turn_id=req.turnId or uuid.uuid4().hex,
                   prompt=prompt, model=req.model, session_id=req.sessionId)
    run = agent.store.claim(persona, CreateTask(prompt=prompt, model=req.model,
        taskId=identifier if existing else None), external=run)
    model = req.model or server._harness_settings()['models'].get(runtime, '')
    if runtime == 'api':
        model = req.model or server.read_settings(server.ENV_FILE).get('EASEL_DIRECT_API_MODEL', '')
    with agent.store.store.connect() as db:
        db.execute('INSERT INTO agent_runtime_turns(persona,task_id,turn_id,owner,channel,model) VALUES(?,?,?,?,?,?)',
                   (run.persona, run.task_id, run.turn_id, worker_owner(), runtime, model))
    if req.attachments or req.quote or req.references:
        agent.store.ingest(run, AgentSnapshot(status='running', events=(AgentEvent(
            uuid=run.turn_id + ':context', type='context', data=req.model_dump(mode='json',
                include={'attachments', 'quote', 'references'})),)))
    return run


async def complete_chat(run: AgentRun | None) -> None:
    """Persist the actual journal/snapshot after the existing producer finalizes."""
    if run is not None:
        agent = service()
        agent.store.ingest(run, await agent.hooks.poll(run))
