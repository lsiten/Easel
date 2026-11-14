"""Hourly source-equivalent preference refresh with a durable process-wide claim."""
from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timezone
import logging
import sqlite3
import time
from typing import TYPE_CHECKING
import anyio
from pydantic import ValidationError
from easel.content_library import ContentLibrary
from easel.content_library_models import ContentError
from easel.generation_draft_memory import DraftMemories
from easel.generation_draft_models import MemoryRegenerate
from easel.generation_models import GenerationError
from easel.publish_recovery import worker_owner

if TYPE_CHECKING:
    from easel.generation_draft import DraftGeneration


async def refresh_due(memory: DraftMemories) -> int:
    """One durable claim per UTC hour; failed or interrupted inference is not blindly repeated."""
    stamp = time.time()
    hour = int(stamp // 3600)
    with memory.store.base.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS draft_memory_refresh(hour INTEGER PRIMARY KEY, owner TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0)')
        claimed = db.execute('INSERT OR IGNORE INTO draft_memory_refresh(hour,owner) VALUES(?,?)', (hour, worker_owner())).rowcount
        if not claimed:
            return 0
        users = {row['persona'] for row in db.execute("SELECT DISTINCT persona FROM draft_jobs WHERE state='succeeded' AND updated_at>?", (stamp-86400,))}
    library = ContentLibrary(memory.store.base.outputs)
    cutoff = datetime.fromtimestamp(stamp-86400, timezone.utc).isoformat()
    with library.connect() as db:
        users.update(row['persona'] for row in db.execute("""SELECT DISTINCT persona FROM content_records
            WHERE domain='material' AND json_extract(body,'$.status')='success'
            AND json_extract(body,'$.options.source')='draft-generation' AND json_extract(body,'$.updated_at')>?""", (cutoff,)))
    failures = 0
    count = 0
    for persona in sorted(users):
        for kind in ('video', 'image-text'):
            try:
                await memory.regenerate(persona, MemoryRegenerate(contentType=kind))
                count += 1
            except (GenerationError, ContentError, ValidationError, OSError, sqlite3.Error) as error:
                failures += 1
                logging.getLogger(__name__).warning('Draft automatic memory refresh failed error_type=%s', type(error).__name__)
    with memory.store.base.connect() as db:
        db.execute('UPDATE draft_memory_refresh SET completed=1,failures=? WHERE hour=?', (failures, hour))
    return count


async def dispatch_draft_memory(factory: Callable[[], DraftGeneration]) -> None:
    while True:
        try:
            await refresh_due(factory().memory)
        except (GenerationError, ContentError, ValidationError, OSError, sqlite3.Error) as error:
            logging.getLogger(__name__).warning('Draft memory scheduler failed error_type=%s', type(error).__name__)
        await anyio.sleep(60)
