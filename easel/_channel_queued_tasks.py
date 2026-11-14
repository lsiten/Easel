"""Restore tasks whose frozen queued run received a fresh credential approval."""
from __future__ import annotations

import json
from pathlib import Path

from easel.publish_models import PublishRun
from easel.publish_tasks import TaskStore


def restore_reviewed_task(root: Path, run: PublishRun) -> None:
    """Repeat safely after interruption between ledger approval and task restoration."""
    if not (root / '_publish/tasks.sqlite3').exists():
        return
    store = TaskStore(root / '_publish')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute("SELECT * FROM tasks WHERE persona=? AND platform=? AND account_uid=? AND state='failed'",
                          (run.persona, run.platform, run.account_id)).fetchall()
        for row in rows:
            payload = json.loads(row['payload_json'])
            if payload.get('idempotency_key') != run.idempotency_key:
                continue
            original = run.payload['channel_approval']
            if payload.get('approval_digest') != original['approval_digest']:
                continue
            result = json.dumps({'run_id': run.run_id, 'state': 'queued'})
            db.execute("UPDATE tasks SET state='queued',run_id=?,owner='',next_attempt=0,result_json=? WHERE id=? AND state='failed'",
                       (run.run_id, result, row['id']))
