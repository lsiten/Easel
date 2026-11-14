"""Concurrent dispatch; SQLite claims enforce global admission and priority pools."""
from __future__ import annotations

from collections.abc import Callable, Awaitable
import logging
import sqlite3
import anyio
from easel.content_library_models import ContentError
from easel.generation_models import GenerationError
from easel.generation_queue import QueuePolicy


async def dispatch_pool(execute: Callable[[], Awaitable[bool]], recover: Callable[[], int], policy: QueuePolicy) -> None:
    active = 0
    logger = logging.getLogger(__name__)

    async def run() -> None:
        nonlocal active
        try:
            await execute()
        except (sqlite3.Error, OSError, GenerationError, ContentError) as error:
            logger.error('Generation dispatcher queue=%s error_type=%s', policy.kind, type(error).__name__)
        finally:
            active -= 1

    async with anyio.create_task_group() as group:
        while True:
            try:
                recover()
                normal, low, _ = policy.limits()
                for _ in range(max(0, normal + low - active)):
                    active += 1
                    group.start_soon(run)
            except (sqlite3.Error, OSError, GenerationError) as error:
                logger.error('Generation dispatcher queue=%s error_type=%s', policy.kind, type(error).__name__)
            await anyio.sleep(0)
            await anyio.sleep(0.2 if active else 2)
