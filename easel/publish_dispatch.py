"""Run due tasks through normal application publishing entry points."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import os
import time

from easel.publish_ledger import PublishLedger, RunState
from easel.publish_recovery import recover_interrupted, worker_owner
from easel.publish_recovery import _owner_alive
from easel.publish_tasks import TaskStore


_TERMINAL = frozenset({'published', 'failed', 'unknown', 'cancelled', 'submitted'})
_LOG = logging.getLogger(__name__)


def _intent_run(ledger: PublishLedger, platform: str, payload: dict):
    return next((run for run in ledger.list_runs(persona=payload['persona'], platform=platform)
                 if run.idempotency_key == payload.get('idempotency_key', payload.get('run_id', ''))), None)


def _task_state(run) -> str:
    return 'queued' if run.state in {RunState.PREPARED, RunState.APPROVED, RunState.QUEUED} else run.state.value


def recover_tasks(store: TaskStore, ledger: PublishLedger) -> None:
    """Resume only intents proven to have no external execution record."""
    with store.connect() as db:
        rows = db.execute("SELECT * FROM tasks WHERE state IN ('dispatching','running','submitted')").fetchall()
        for row in rows:
            run = ledger.get_run(row['run_id']) if row['run_id'] else None
            if run is None:
                payload = json.loads(row['payload_json'])
                run = next((candidate for candidate in ledger.list_runs(persona=row['persona'], platform=row['platform'])
                            if candidate.idempotency_key == payload.get('idempotency_key', row['id'])), None)
            if run is not None:
                with ledger._connect() as progress_db:
                    active = progress_db.execute('SELECT 1 FROM publish_leases WHERE run_id=? AND expires_at>?', (run.run_id, time.time())).fetchone()
                if row['state'] == 'dispatching' and active and _owner_alive(row['owner']):
                    continue
                state = _task_state(run)
                db.execute('UPDATE tasks SET state=?,run_id=? WHERE id=?', (state, run.run_id, row['id']))
                continue
            pid = row['owner'].partition(':')[0]
            try:
                os.kill(int(pid), 0)
                alive = True
            except (ValueError, ProcessLookupError):
                alive = False
            except PermissionError:
                alive = True
            if not alive:
                db.execute("UPDATE tasks SET state='queued',owner='' WHERE id=?", (row['id'],))


async def dispatch_one(store: TaskStore, ledger: PublishLedger,
                       submit: Callable[[str, dict], Awaitable[tuple[int, dict]]]) -> bool:
    owner = worker_owner()
    claimed = store.claim(owner, time.time())
    if claimed is None:
        return False
    task_id, platform, payload = claimed
    try:
        status, result = await submit(platform, payload)
    except Exception as error:
        run = _intent_run(ledger, platform, payload)
        _LOG.error('Scheduled publish interrupted task=%s error_type=%s', task_id, type(error).__name__)
        store.finish(task_id, owner, _task_state(run) if run else 'unknown',
                     {'run_id': run.run_id if run else '', 'message': '提交连接中断，请核对平台结果'})
        return True
    run_id = result.get('run_id', '')
    run = ledger.get_run(run_id) if run_id else None
    if run is None:
        run = _intent_run(ledger, platform, payload)
    if run is not None:
        state = _task_state(run)
        result = {**result, 'run_id': run.run_id, 'state': state}
    elif status >= 500:
        state = 'unknown'
    elif status >= 400:
        state = 'failed'
        result = {'message': '发布预检或授权失败，请重新审核', 'state': state}
    else:
        state = str(result.get('state', 'unknown'))
        if state not in _TERMINAL and state not in {'queued', 'running'}:
            state = 'unknown'
    store.finish(task_id, owner, state, result)
    return True


async def dispatch_loop(store_factory: Callable[[], TaskStore], ledger_factory: Callable[[], PublishLedger],
                        submit: Callable[[str, dict], Awaitable[tuple[int, dict]]],
                        finalize: Callable[[str, str, dict], Awaitable[tuple[int, dict]]] | None = None,
                        reconcile: Callable[[str, str, dict], Awaitable[tuple[int, dict]]] | None = None,
                        prepare: Callable[[str, dict], dict] | None = None,
                        process_events: Callable[[], int] | None = None) -> None:
    while True:
        store, ledger = store_factory(), ledger_factory()
        recover_interrupted(ledger)
        recover_tasks(store, ledger)
        if process_events is not None:
            await asyncio.to_thread(process_events)
        handled = False
        if prepare is not None:
            from easel.publish_task_preparation import prepare_one
            handled = await asyncio.to_thread(prepare_one, store, prepare)
        if not handled:
            handled = await dispatch_one(store, ledger, submit)
        if not handled and finalize is not None:
            from easel.channel_monitor import monitor_one
            handled = await monitor_one(ledger, ledger.path.parent.parent, finalize, reconcile)
        await asyncio.sleep(0 if handled else 2)
