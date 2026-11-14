"""Bounded native-ID reconciliation for Aideo jobs across all local owners."""
from __future__ import annotations

from collections.abc import Callable
import logging
import sqlite3
from typing import Final

import anyio
from pydantic import ValidationError

from easel.generation_aideo_models import AideoJob
from easel.generation_aideo_service import AideoGeneration
from easel.generation_models import GenerationError

POLL_INTERVAL: Final = 30
BATCH_SIZE: Final = 100
CONCURRENCY: Final = 4


async def reconcile_pending(generation: AideoGeneration) -> None:
    """Read known native receipts only; no submission path is reachable here."""
    limiter = anyio.CapacityLimiter(CONCURRENCY)

    async def reconcile(job: AideoJob) -> None:
        async with limiter:
            try:
                await generation.get(job.persona, job.id)
            except (GenerationError, ValidationError, OSError, sqlite3.Error) as error:
                logging.getLogger(__name__).warning('Aideo reconciliation failed task_id=%s error_type=%s', job.id, type(error).__name__)

    jobs = generation.store.pending(BATCH_SIZE)
    async with anyio.create_task_group() as tasks:
        for job in jobs:
            tasks.start_soon(reconcile, job)


async def dispatch_aideo(factory: Callable[[], AideoGeneration]) -> None:
    """Resume durable receipts on startup and every source-equivalent 30 seconds."""
    while True:
        try:
            await reconcile_pending(factory())
        except (GenerationError, ValidationError, OSError, sqlite3.Error) as error:
            logging.getLogger(__name__).warning('Aideo dispatcher failed error_type=%s', type(error).__name__)
        await anyio.sleep(POLL_INTERVAL)
