"""Owner-bound replies to the existing Harness question future."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from easel.generation_models import GenerationError
from easel.generation_router import generation_errors
from web.generation_agent_bridge import authorize, service
from web.generation_agent_media import session_id
from web import app as server


class AgentAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    answers: dict[str, list[str]] = Field(min_length=1, max_length=20)


class QuestionIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra='ignore')
    id: str


def create_router() -> APIRouter:
    router = APIRouter(prefix='/api/ai/agent/tasks', tags=['agent-tasks'])

    @router.post('/{task_id}/questions/{question_id}')
    async def answer(task_id: str, question_id: str, payload: AgentAnswer, request: Request, persona: str = ''):
        owner = authorize(request, persona)
        with generation_errors():
            run = service().store.run(owner, task_id)
            matched = False
            for event in server._read_job_events(run.turn_id):
                if event['event'] == 'question' and isinstance(event['data'], str):
                    try:
                        matched = matched or QuestionIdentity.model_validate_json(event['data']).id == question_id
                    except ValidationError:
                        continue
            pending = server._HARNESS_QUESTIONS.get(question_id)
            if not matched or pending is None or pending['session'] != server._eff_sk(session_id(run), owner):
                raise GenerationError('agent_question_not_found', 404)
            return await server.api_question_answer(server.QuestionAnswerRequest(
                sessionId=session_id(run), questionId=question_id, answers=payload.answers))

    return router
