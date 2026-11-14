"""Recover interrupted external publication without repeating the submission."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import json
from typing import TYPE_CHECKING
import uuid

from easel.publish_models import _json, _now

if TYPE_CHECKING:
    from easel.publish_ledger import PublishLedger


def worker_owner() -> str:
    return f'{os.getpid()}:{uuid.uuid4().hex}'


def _owner_alive(owner: str) -> bool:
    pid, separator, _nonce = owner.partition(':')
    if not separator or not pid.isdecimal():
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def recover_interrupted(ledger: PublishLedger) -> tuple[str, ...]:
    """Fence abandoned workers and preserve ambiguity after an external call."""
    recovered: list[str] = []
    now = datetime.now(timezone.utc).timestamp()
    with ledger._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('''SELECT r.*, l.owner, l.expires_at FROM publish_runs r
            LEFT JOIN publish_leases l ON l.run_id=r.run_id
            WHERE r.state IN ('running', 'submitted', 'reconciling')''').fetchall()
        for row in rows:
            if row['owner'] and row['expires_at'] > now and _owner_alive(row['owner']):
                continue
            progress = json.loads(row['payload_json']).get('channel_progress', {})
            receipt = progress.get('receipt') or {}
            safe_cover = row['platform'] == 'youtube' and progress.get('stage') == 'publication_cover' and bool(row['platform_work_id']) and receipt.get('platform_work_id') == row['platform_work_id']
            if row['state'] == 'submitted' and (not progress.get('inflight', False) or safe_cover):
                continue
            db.execute('''UPDATE publish_runs SET state='unknown', updated_at=?,
                generation=generation+1, last_error='worker_interrupted' WHERE run_id=?''',
                (_now(), row['run_id']))
            db.execute('''UPDATE publish_leases SET expires_at=0, fencing_token=fencing_token+1
                WHERE run_id=?''', (row['run_id'],))
            db.execute('''INSERT INTO publish_events(event_id,run_id,sequence,event_type,
                from_state,to_state,actor,payload_json,created_at)
                VALUES(?, ?, (SELECT COALESCE(MAX(sequence),0)+1 FROM publish_events WHERE run_id=?),
                    'recovery', ?, 'unknown', 'recovery', ?, ?)''',
                (uuid.uuid4().hex, row['run_id'], row['run_id'], row['state'],
                 _json({'error': 'worker_interrupted'}), _now()))
            recovered.append(row['run_id'])
    return tuple(recovered)
