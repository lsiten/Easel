"""Durable provider progress polling and verified webhook read-back scheduling."""
from collections.abc import Awaitable, Callable
import hashlib
import json
from pathlib import Path
import time

from easel.channels import CredentialStore
from easel.channels.completion_events import ack_verified_event, load_verified_events
from easel.publish_ledger import PublishLedger
from easel._channel_publish_cover import thumbnail_recovery


class ChannelMonitor:
    def __init__(self, ledger: PublishLedger) -> None:
        self.ledger = ledger
        with ledger._connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS channel_poll_schedule(
                run_id TEXT PRIMARY KEY REFERENCES publish_runs(run_id) ON DELETE CASCADE,next_poll REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS channel_hook_readbacks(
                event_key TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES publish_runs(run_id) ON DELETE CASCADE,
                work_id TEXT NOT NULL,platform TEXT NOT NULL,scope_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0,PRIMARY KEY(event_key,run_id,work_id));''')

    def consume_webhooks(self, outputs: Path) -> int:
        if not (outputs / '_channels/channels.sqlite3').is_file():
            return 0
        source = CredentialStore(outputs / '_channels')
        events = load_verified_events(source)
        count = 0
        for event in events:
            key = tuple(str(event[field]) for field in ('event_id', 'persona', 'platform', 'account_uid'))
            event_key = hashlib.sha256(json.dumps(key).encode()).hexdigest()
            with self.ledger._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                runs = db.execute("SELECT * FROM publish_runs WHERE persona=? AND platform=? AND account_id=? AND state IN ('submitted','unknown')",
                                  (event['persona'], event['platform'], event['account_uid'])).fetchall()
                for row in runs:
                    if row['state'] == 'submitted':
                        db.execute('''INSERT INTO channel_poll_schedule VALUES(?,0)
                            ON CONFLICT(run_id) DO UPDATE SET next_poll=0''', (row['run_id'],))
                        continue
                    hints = event.get('events', [])
                    for hint in hints if isinstance(hints, list) else []:
                        if not isinstance(hint, dict) or not row['external_request_id']:
                            continue
                        if hint.get('external_request_id') != row['external_request_id']:
                            continue
                        work_id = hint.get('platform_work_id')
                        if not isinstance(work_id, str) or not work_id:
                            continue
                        scope = {'persona': row['persona'], 'account_uid': row['account_id'], 'platform_work_id': work_id}
                        db.execute('''INSERT OR IGNORE INTO channel_hook_readbacks
                            (event_key,run_id,work_id,platform,scope_json) VALUES(?,?,?,?,?)''',
                            (event_key, row['run_id'], work_id, row['platform'], json.dumps(scope)))
            ack_verified_event(source, *key)
            count += 1
        return count

    def claim(self, now: float) -> tuple[str, str, dict] | None:
        with self.ledger._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('''SELECT r.* FROM publish_runs r
                LEFT JOIN channel_poll_schedule p ON p.run_id=r.run_id
                LEFT JOIN publish_leases l ON l.run_id=r.run_id
                WHERE r.state='submitted' AND COALESCE(p.next_poll,0)<=?
                AND COALESCE(l.expires_at,0)<=? ORDER BY COALESCE(p.next_poll,0),r.created_at''', (now, now)).fetchall()
            for row in rows:
                payload = json.loads(row['payload_json'])
                progress = payload.get('channel_progress')
                native_handoff = row['platform'] == 'douyin-open' and payload.get('handoff_template') and payload.get('auth_mode') == 'app_handoff'
                if not isinstance(progress, dict) and not native_handoff:
                    continue
                progress = progress or {}
                if row['platform'] == 'wechat-channels-open' and progress.get('stage') == 'publication_user_action':
                    continue
                run = self.ledger._row_to_run(row)
                if progress.get('inflight') and not thumbnail_recovery(run):
                    continue
                db.execute('''INSERT INTO channel_poll_schedule VALUES(?,?)
                    ON CONFLICT(run_id) DO UPDATE SET next_poll=excluded.next_poll''', (row['run_id'], now + 15))
                return row['platform'], row['run_id'], {'persona': row['persona'], 'account_uid': row['account_id']}
        return None

    def claim_readback(self, now: float) -> dict | None:
        with self.ledger._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM channel_hook_readbacks WHERE state='pending' AND next_attempt<=? ORDER BY next_attempt LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            db.execute('''UPDATE channel_hook_readbacks SET next_attempt=?,attempts=attempts+1
                WHERE event_key=? AND run_id=? AND work_id=?''', (now + 60, row['event_key'], row['run_id'], row['work_id']))
            return dict(row)

    def finish_readback(self, item: dict, verified: bool) -> None:
        state = 'succeeded' if verified else 'parked' if item['attempts'] >= 9 else 'pending'
        with self.ledger._connect() as db:
            db.execute('UPDATE channel_hook_readbacks SET state=? WHERE event_key=? AND run_id=? AND work_id=?',
                       (state, item['event_key'], item['run_id'], item['work_id']))


async def monitor_one(ledger: PublishLedger, outputs: Path,
                      finalize: Callable[[str, str, dict], Awaitable[tuple[int, dict]]],
                      reconcile: Callable[[str, str, dict], Awaitable[tuple[int, dict]]] | None = None) -> bool:
    monitor = ChannelMonitor(ledger)
    monitor.consume_webhooks(outputs)
    pending = monitor.claim(time.time())
    if pending is not None:
        platform, run_id, scope = pending
        try:
            await finalize(platform, run_id, scope)
        except (OSError, TimeoutError, ValueError):
            pass
        return True
    item = monitor.claim_readback(time.time()) if reconcile is not None else None
    if item is None:
        return False
    try:
        status, result = await reconcile(item['platform'], item['run_id'], json.loads(item['scope_json']))
    except (OSError, TimeoutError, ValueError):
        return True
    monitor.finish_readback(item, status < 400 and result.get('verification_outcome') == 'verified')
    return True
