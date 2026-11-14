"""Adapt durable Agent tasks to the running Easel Harness and its existing journal."""
from contextvars import ContextVar

import anyio
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError
from sse_starlette.sse import EventSourceResponse

from easel.generation_agent import AgentService
from easel.generation_agent_models import AgentEvent, AgentRun, AgentSnapshot
from easel.generation_agent_store import AgentStore
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.publish_recovery import _owner_alive, worker_owner
from web.generation_agent_media import session_id, stage_prompt
from web import app as server

INTERNAL_AGENT_CHAT: ContextVar[bool] = ContextVar('internal_agent_chat', default=False)

_ORIGIN: ContextVar[str] = ContextVar('generation_agent_origin', default='')


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    id: int = Field(gt=0)
    event: str
    data: JsonValue = None


class RuntimeTurn(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)
    status: str = ''
    text: str = ''
    turn_id: str = ''
    stop_reason: str = ''
    clean_end: bool = False


def authorize(request: Request, persona: str) -> str:
    if request.method != 'GET' or not getattr(request.state, 'caller_key', None):
        server._require_side_effect_access(request)
    if persona and (not server.valid_persona_name(persona) or not server.profile_exists(persona)):
        raise HTTPException(400, 'persona_invalid')
    identity = getattr(request.state, 'caller_key', None)
    if identity is not None and identity['persona'] != persona:
        raise HTTPException(403, 'persona_scope_conflict')
    _ORIGIN.set(str(request.base_url))
    return persona


async def _drain(response: EventSourceResponse) -> None:
    async for _event in response.body_iterator:
        pass


class AgentRuntimeBridge:
    def __init__(self, store: GenerationStore) -> None:
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS agent_runtime_turns(
                persona TEXT NOT NULL,task_id TEXT NOT NULL,turn_id TEXT PRIMARY KEY,owner TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT '',model TEXT NOT NULL DEFAULT '')''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(agent_runtime_turns)')}
            for name in ('channel', 'model'):
                if name not in columns:
                    db.execute(f"ALTER TABLE agent_runtime_turns ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    async def start(self, run: AgentRun) -> None:
        runtime = server._chat_backend(session_id(run), run.persona)
        if runtime == 'api' and not run.session_id:
            raise GenerationError('agent_tool_runtime_required', 409)
        model = run.model or server._harness_settings()['models'].get(runtime, '')
        with self.store.connect() as db:
            db.execute('INSERT INTO agent_runtime_turns(persona,task_id,turn_id,owner,channel,model) VALUES(?,?,?,?,?,?)',
                       (run.persona, run.task_id, run.turn_id, worker_owner(), runtime, model))
        message, attachments = await anyio.to_thread.run_sync(stage_prompt, run, _ORIGIN.get())
        task = AgentStore(self.store).get(run.persona, run.task_id)
        if task.status != 'running':
            raise GenerationError('agent_task_not_running', 409)
        token = INTERNAL_AGENT_CHAT.set(True)
        try:
            response = await server.api_chat_stream(server.ChatRequest(message=message, attachments=attachments,
                persona=run.persona, sessionId=session_id(run), turnId=run.turn_id, model=run.model))
        finally:
            INTERNAL_AGENT_CHAT.reset(token)
        server.app.state.generation_tasks.start_soon(_drain, response)

    async def poll(self, run: AgentRun) -> AgentSnapshot:
        raw = TypeAdapter(tuple[RuntimeEvent, ...]).validate_python(server._read_job_events(run.turn_id))
        events = [AgentEvent(uuid=f'{run.turn_id}:{event.id}', type=event.event, data=event.data) for event in raw]
        path = server._turn_file('web:' + server._eff_sk(session_id(run), run.persona))
        try:
            turn = RuntimeTurn.model_validate_json(path.read_bytes()) if path.is_file() else RuntimeTurn()
        except (OSError, ValidationError):
            turn = RuntimeTurn()
        active = any(not task.done() and task.get_name() == 'chat:' + run.turn_id for task in server._BG_TASKS)
        if turn.turn_id == run.turn_id and turn.status == 'done':
            if turn.text:
                events.append(AgentEvent(uuid=run.turn_id + ':assistant', type='assistant', data=turn.text))
            status = 'completed' if turn.clean_end and not any(event.type == 'error' for event in events) else 'error'
            if turn.stop_reason in {'stopped', 'user_stopped'}:
                status = 'aborted'
            if turn.stop_reason == 'requires_action':
                status = 'requires_action'
            return AgentSnapshot(events=tuple(events), status=status)
        with self.store.connect() as db:
            owner = db.execute('SELECT owner FROM agent_runtime_turns WHERE persona=? AND task_id=? AND turn_id=?',
                               (run.persona, run.task_id, run.turn_id)).fetchone()
        remote_active = owner is not None and owner['owner'].split(':', 1)[0] != worker_owner().split(':', 1)[0] and _owner_alive(owner['owner'])
        if active or remote_active:
            return AgentSnapshot(events=tuple(events), status='running')
        events.append(AgentEvent(uuid=run.turn_id + ':interrupted', type='error', data='agent_runtime_interrupted'))
        return AgentSnapshot(events=tuple(events), status='error')

    async def stop(self, run: AgentRun) -> None:
        with self.store.connect() as db:
            row = db.execute('SELECT turn_id FROM agent_tasks WHERE id=? AND persona=?', (run.task_id, run.persona)).fetchone()
        if row is None or row['turn_id'] != run.turn_id:
            return
        result = await server.api_chat_stop(server.StopRequest(sessionId=session_id(run), persona=run.persona))
        if not result['stopped']:
            for task in tuple(server._BG_TASKS):
                if not task.done() and task.get_name() == 'chat:' + run.turn_id:
                    task.cancel()
        with anyio.move_on_after(10):
            while any(not task.done() and task.get_name() == 'chat:' + run.turn_id for task in server._BG_TASKS):
                await anyio.sleep(0.05)

    async def delete(self, run: AgentRun) -> None:
        await server.api_delete_session(server._eff_sk(session_id(run), run.persona))
        with self.store.connect() as db:
            turns = tuple(row['turn_id'] for row in db.execute('SELECT turn_id FROM agent_runtime_turns WHERE persona=? AND task_id=?',
                                                              (run.persona, run.task_id)))
            db.execute('DELETE FROM agent_runtime_turns WHERE persona=? AND task_id=?', (run.persona, run.task_id))
        for turn_id in turns:
            server._job_event_file(turn_id).unlink(missing_ok=True)


def service() -> AgentService:
    store = GenerationStore(server.OUTPUTS_DIR)
    return AgentService(AgentStore(store), AgentRuntimeBridge(store))
