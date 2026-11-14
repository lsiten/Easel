"""Freeze reviewed scheduled intents outside the task-store write transaction."""
from collections.abc import Callable
import json
import time
from typing import TYPE_CHECKING

from easel.publish_models import LeaseBusy

if TYPE_CHECKING:
    from easel.publish_tasks import TaskStore


def prepare_one(store: 'TaskStore', prepare: Callable[[str, dict], dict], task_id: str = '') -> bool:
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM tasks WHERE state='preparing' AND next_attempt<=? AND (?='' OR id=?) ORDER BY rowid LIMIT 1",
                         (time.time(), task_id, task_id)).fetchone()
        if row is None:
            return False
        db.execute('UPDATE tasks SET next_attempt=? WHERE id=?', (time.time()+30, row['id']))
    try:
        result = prepare(row['platform'], json.loads(row['payload_json']))
    except LeaseBusy:
        return True
    except Exception as error:
        result = {'state': 'failed', 'error': 'preparation_' + type(error).__name__}
    state = result.get('state', 'failed')
    if state not in {'queued', 'submitted', 'published', 'unknown', 'cancelled', 'failed'}:
        state = 'failed'
    public = {key: result[key] for key in ('state', 'run_id', 'error', 'cover_status', 'cover_error') if key in result}
    with store.connect() as db:
        db.execute("UPDATE tasks SET state=?,run_id=?,result_json=?,next_attempt=0 WHERE id=? AND state='preparing'",
                   (state, result.get('run_id', ''), json.dumps(public), row['id']))
    return True
