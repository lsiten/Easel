"""Transactional chat claims and durable SSE replay."""
from __future__ import annotations
from easel.queue_metrics import install_metrics, CHAT_QUEUE

import hashlib
import time
import uuid
from typing import Literal
from easel.generation_models import GenerationError
from easel.generation_store import GenerationStore
from easel.generation_chat_models import ChatRecord, ChatRequest


class ChatStore:
    def __init__(self, store: GenerationStore) -> None:
        self.base = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS chat_jobs(
                id TEXT PRIMARY KEY, persona TEXT NOT NULL, request_key TEXT NOT NULL,
                digest TEXT NOT NULL, state TEXT NOT NULL, channel TEXT NOT NULL,
                request_model TEXT NOT NULL, operation TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, body TEXT NOT NULL,
                UNIQUE(persona,request_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS chat_events(
                job_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL,
                PRIMARY KEY(job_id,sequence))''')

            install_metrics(db, CHAT_QUEUE)

    def replay(self, request: ChatRequest, operation: Literal['completion', 'stream', 'claude', 'claude_completion']) -> ChatRecord | None:
        """Check the original intent before consulting mutable config or source files."""
        digest = hashlib.sha256((operation + request.model_dump_json()).encode()).hexdigest()
        with self.base.connect() as db:
            row = db.execute('SELECT digest,body FROM chat_jobs WHERE persona=? AND request_key=?',
                             (request.persona, request.request_key)).fetchone()
        if row is None:
            return None
        if row['digest'] != digest:
            raise GenerationError('chat_request_conflict', 409)
        return ChatRecord.model_validate_json(row['body'])

    def claim(self, request: ChatRequest, channel: str, operation: Literal['completion', 'stream', 'claude', 'claude_completion']) -> tuple[ChatRecord, bool]:
        digest = hashlib.sha256((operation + request.model_dump_json()).encode()).hexdigest()
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM chat_jobs WHERE persona=? AND request_key=?',
                             (request.persona, request.request_key)).fetchone()
            if row is not None:
                if row['digest'] != digest:
                    raise GenerationError('chat_request_conflict', 409)
                return ChatRecord.model_validate_json(row['body']), False
            stamp = time.time()
            record = ChatRecord(id=uuid.uuid4().hex, persona=request.persona, channel=channel,
                                request_model=request.model, operation=operation, created_at=stamp, updated_at=stamp,
                                request=request.model_dump(mode='json', exclude={'request_key'}))
            db.execute('INSERT INTO chat_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (record.id, record.persona, request.request_key, digest, record.state, channel,
                        request.model, operation, stamp, stamp, record.model_dump_json()))
            return record, True

    def finish(self, record: ChatRecord) -> None:
        updated = record.model_copy(update={'updated_at': time.time()})
        with self.base.connect() as db:
            db.execute('UPDATE chat_jobs SET state=?,updated_at=?,body=? WHERE id=? AND persona=?',
                       (updated.state, updated.updated_at, updated.model_dump_json(), record.id, record.persona))

    def append(self, identifier: str, event: str) -> int:
        with self.base.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM chat_events WHERE job_id=?', (identifier,)).fetchone()[0]
            db.execute('INSERT INTO chat_events VALUES(?,?,?)', (identifier, sequence, f'id: {sequence}\n' + event))
            return sequence

    def events(self, record: ChatRecord, after: int = 0) -> tuple[str, ...]:
        with self.base.connect() as db:
            return tuple(row['event'] for row in db.execute(
                'SELECT event FROM chat_events WHERE job_id=? AND sequence>? ORDER BY sequence', (record.id, after)))
