"""Paged generation metadata across image and native video queues."""
import sqlite3

from easel.generation_models import (GenerationError, GenerationModel, GenerationPage, GenerationState,
                                     GenerationStatus, GenerationSummary)


class GenerationLogQuery(GenerationModel):
    persona: str
    page: int = 1
    page_size: int = 30
    state: GenerationState | None = None
    model: str = ''
    status: GenerationStatus | None = None
    kind: str = ''
    channel: str = ''


def generation_log_page(db: sqlite3.Connection, query: GenerationLogQuery) -> GenerationPage:
    if query.page < 1 or not 1 <= query.page_size <= 200:
        raise GenerationError('generation_pagination_invalid')
    projection = '''SELECT id,persona,state,kind,channel,request_model AS model,
        operation,created_at,updated_at FROM generation_jobs WHERE persona=?'''
    parameters: list[str | int | None] = [query.persona]
    video_columns = {row['name'] for row in db.execute('PRAGMA table_info(video_jobs)')}
    if video_columns:
        channel = 'channel' if 'channel' in video_columns else "'openai'"
        model = 'request_model' if 'request_model' in video_columns else 'model'
        projection += f''' UNION ALL SELECT id,persona,state,'video' AS kind,{channel} AS channel,
            {model} AS model,operation,created_at,updated_at FROM video_jobs WHERE persona=?'''
        parameters.append(query.persona)
    for table, kind in (('chat_jobs', 'chat'), ('aideo_jobs', 'aideo'), ('draft_jobs', 'draft-generation'),
                        ('direct_edit_jobs', 'aideo'), ('character_jobs', 'character')):
        columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        if columns:
            projection += f''' UNION ALL SELECT id,persona,state,'{kind}' AS kind,channel,request_model AS model,
                operation,created_at,updated_at FROM {table} WHERE persona=?'''
            parameters.append(query.persona)
    agent_columns = {row['name'] for row in db.execute('PRAGMA table_info(agent_tasks)')}
    if agent_columns:
        runtime_columns = {row['name'] for row in db.execute('PRAGMA table_info(agent_runtime_turns)')}
        channel = "COALESCE(r.channel,'agent')" if 'channel' in runtime_columns else "'agent'"
        model = "COALESCE(r.model,'')" if 'model' in runtime_columns else "''"
        join = ' LEFT JOIN agent_runtime_turns r ON r.turn_id=t.turn_id AND r.persona=t.persona' if runtime_columns else ''
        projection += f''' UNION ALL SELECT t.id,t.persona,CASE t.status WHEN 'completed' THEN 'succeeded'
            WHEN 'requires_action' THEN 'waiting' WHEN 'aborted' THEN 'failed' WHEN 'error' THEN 'failed'
            ELSE 'running' END AS state,'agent' AS kind,{channel} AS channel,{model} AS model,'agent' AS operation,
            json_extract(t.body,'$.createdAt') AS created_at,json_extract(t.body,'$.updatedAt') AS updated_at
            FROM agent_tasks t {join} WHERE t.persona=? AND t.deleted=0'''
        parameters.append(query.persona)
    where = '''WHERE (? IS NULL OR state=?) AND (?='' OR model=?) AND (?='' OR kind=?) AND (?='' OR channel=?)
        AND (? IS NULL OR (?='generating' AND state IN ('queued','running','submitting','waiting','unknown')) OR
        (?='success' AND state='succeeded') OR (?='failed' AND state='failed'))'''
    parameters.extend((query.state, query.state, query.model, query.model, query.kind, query.kind, query.channel,
                       query.channel, query.status, query.status, query.status, query.status))
    selection = 'FROM (' + projection + ') ' + where
    total = db.execute('SELECT COUNT(*) ' + selection, parameters).fetchone()[0]
    rows = db.execute('SELECT * ' + selection + ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                      (*parameters, query.page_size, (query.page - 1) * query.page_size))
    summaries = tuple(GenerationSummary.model_validate(dict(row)) for row in rows)
    return GenerationPage(items=summaries, total=total, page=query.page, page_size=query.page_size)
