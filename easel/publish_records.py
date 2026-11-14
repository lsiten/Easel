"""Scoped history views retaining durable idempotency tombstones."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from easel.publish_ledger import PublishLedger
from easel.publish_models import PublishRun, RunState, _now


class PublishRecords:
    def __init__(self, ledger: PublishLedger) -> None:
        self.ledger = ledger
        with ledger._connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS publish_record_views(
                run_id TEXT PRIMARY KEY REFERENCES publish_runs(run_id) ON DELETE CASCADE,
                hidden INTEGER NOT NULL DEFAULT 0,annotation_json TEXT NOT NULL DEFAULT '{}');''')

    def get(self, persona: str, run_id: str) -> PublishRun:
        run = self.ledger.get_run(run_id)
        with self.ledger._connect() as db:
            hidden = db.execute('SELECT hidden FROM publish_record_views WHERE run_id=?', (run_id,)).fetchone()
        if run is None or run.persona != persona or (hidden and hidden['hidden']):
            raise KeyError(run_id)
        return run

    def view(self, run: PublishRun, *, public: bool = False) -> dict:
        result = {'run_id': run.run_id, 'platform': run.platform, 'state': run.state.value,
                  'platform_work_id': run.platform_work_id, 'permalink': run.permalink,
                  'created_at': run.created_at, 'updated_at': run.updated_at}
        with self.ledger._connect() as db:
            row = db.execute('SELECT annotation_json FROM publish_record_views WHERE run_id=?', (run.run_id,)).fetchone()
        annotation = json.loads(row['annotation_json']) if row else {}
        result['work_link_status'] = annotation.get('work_link_status', 'ready' if run.permalink else 'pending')
        if not public:
            result.update(persona=run.persona, account_uid=run.account_id, attempts=run.attempts,
                          title=run.payload.get('title', ''), last_error=run.last_error)
            progress = run.payload.get('channel_progress') or {}
            receipt = progress.get('receipt') or {}
            details = receipt.get('details') or {}
            result.update(cover_status=progress.get('cover_status') or details.get('cover_status', ''),
                          cover_error=progress.get('cover_error', ''), visibility=details.get('visibility', ''))
        return result

    def list(self, persona: str, *, platform: str = '', account_uid: str = '',
             statuses: tuple[str, ...] = (), start: datetime | None = None,
             end: datetime | None = None, offset: int = 0, limit: int = 100) -> dict:
        if any(stamp is not None and stamp.tzinfo is None for stamp in (start, end)):
            raise ValueError('时间筛选必须包含时区')
        with self.ledger._connect() as db:
            hidden = {row['run_id'] for row in db.execute('SELECT run_id FROM publish_record_views WHERE hidden=1')}
        runs = [run for run in self.ledger.list_runs(persona=persona, platform=platform)
                if run.run_id not in hidden and (not account_uid or run.account_id == account_uid)
                and (not statuses or run.state.value in statuses)
                and (start is None or datetime.fromisoformat(run.created_at) >= start)
                and (end is None or datetime.fromisoformat(run.created_at) <= end)]
        return {'total': len(runs), 'items': [self.view(run) for run in runs[offset:offset + limit]]}

    def hide(self, persona: str, run_id: str) -> None:
        with self.ledger._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM publish_runs WHERE run_id=? AND persona=?', (run_id, persona)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row['state'] not in {'published', 'failed', 'cancelled'}:
                raise ValueError('仅可删除已结束的本地记录')
            pending = db.execute("SELECT 1 FROM publish_effects WHERE run_id=? AND status!='succeeded'", (run_id,)).fetchone()
            if pending:
                raise ValueError('记录仍有待完成的回写')
            db.execute('''INSERT INTO publish_record_views(run_id,hidden) VALUES(?,1)
                ON CONFLICT(run_id) DO UPDATE SET hidden=1''', (run_id,))

    def annotate_link(self, persona: str, run_id: str, status: str, message: str = '') -> None:
        self.get(persona, run_id)
        with self.ledger._connect() as db:
            db.execute('''INSERT INTO publish_record_views(run_id,annotation_json) VALUES(?,?)
                ON CONFLICT(run_id) DO UPDATE SET annotation_json=excluded.annotation_json''',
                (run_id, json.dumps({'work_link_status': status, 'message': message[:200], 'updated_at': _now()})))

    def summary(self, persona: str, start: datetime | None = None, end: datetime | None = None) -> dict:
        if any(stamp is not None and stamp.tzinfo is None for stamp in (start, end)):
            raise ValueError('时间筛选必须包含时区')
        records = self.list(persona, statuses=('published',), limit=10**9)['items']
        daily: dict[str, int] = {}
        platforms: dict[str, int] = {}
        total = 0
        for record in records:
            run = self.get(persona, record['run_id'])
            stamp = str(run.payload.get('published_at') or run.updated_at)
            published = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if (start is not None and published < start) or (end is not None and published > end):
                continue
            total += 1
            day = published.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            daily[day] = daily.get(day, 0) + 1
            platforms[run.platform] = platforms.get(run.platform, 0) + 1
        return {'total': total, 'daily': [{'date': day, 'count': daily[day]} for day in sorted(daily)], 'platforms': platforms}
