"""Legacy schema and approved media regressions."""
import json
import sqlite3

import pytest

from easel.ownership import OwnershipError, build_media_manifest, freeze_media
from easel.publish_ledger import PublishLedger


def test_old_schema_retains_runs_and_consolidates_effect_receipts(tmp_path):
    path = tmp_path / 'ledger.db'
    ledger = PublishLedger(path)
    run = ledger.create_run(platform='x', persona='alice', idempotency_key='old',
                            approval_digest='a', media_manifest_hash='m')
    ledger.record_effect(run.run_id, effect_key='first', effect_type='calendar')
    with sqlite3.connect(path) as db:
        db.execute('DROP INDEX idx_publish_effects_once')
        db.execute("INSERT INTO publish_effects SELECT 'second',run_id,effect_type,'succeeded',payload_json,created_at,created_at FROM publish_effects")
        for column in ('external_request_id', 'platform_work_id', 'permalink'):
            db.execute(f'ALTER TABLE publish_runs DROP COLUMN {column}')
        db.execute('DELETE FROM schema_migrations WHERE version=2')
    restored = PublishLedger(path)
    assert restored.get_run(run.run_id).approval_digest == 'a'
    assert restored.pending_effects() == ()
    assert restored.list_events(run.run_id)[-1].payload == {'retained': 'second', 'superseded': ['first']}
    assert restored.integrity_check() == ('ok',)


def test_changed_source_requires_review_and_frozen_media_survives_source_edits(tmp_path):
    project = tmp_path / 'post'
    project.mkdir()
    (project / '.easel.json').write_text(json.dumps({'profile': 'alice'}))
    media = project / 'image.jpg'
    media.write_bytes(b'approved')
    manifest = build_media_manifest(tmp_path, 'alice', ['post/image.jpg'])
    media.write_bytes(b'changed')
    with pytest.raises(OwnershipError, match='重新审核'):
        freeze_media(tmp_path, 'alice', 'a' * 32, manifest)
    media.write_bytes(b'approved')
    frozen = freeze_media(tmp_path, 'alice', 'b' * 32, manifest)
    media.write_bytes(b'edited after approval')
    assert frozen[0].read_bytes() == b'approved'
