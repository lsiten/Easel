from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest

from easel.publish_ledger import PublishLedger
from easel.publish_models import RunState
from easel.publish_projection import (PublishedProjection, ProjectionError,
                                      deliver_publish_projection, drain_publish_projections,
                                      queue_publish_projection)


def published(ledger: PublishLedger, persona: str = 'alice'):
    run = ledger.create_run(platform='xiaohongshu', persona=persona, idempotency_key='post',
                            approval_digest='a', media_manifest_hash='m', account_id=f'{persona}::xiaohongshu',
                            payload={'title': 'A verified post', 'platform_name': '小红书',
                                     'tags': 'AI', 'publish_type': '图文'})
    for expected, target in ((RunState.PREPARED, RunState.APPROVED),
                             (RunState.APPROVED, RunState.QUEUED),
                             (RunState.QUEUED, RunState.RUNNING),
                             (RunState.RUNNING, RunState.PUBLISHED)):
        run = ledger.transition(run.run_id, expected=expected, target=target, actor='test')
    return run


def rows(outputs: Path, persona: str = 'alice'):
    calendar = json.loads((outputs / '_schedule' / f'{persona}.json').read_text())
    publish_log = json.loads((outputs / '_analytics' / 'publish-log' / f'{persona}.json').read_text())
    return calendar, publish_log['entries']


def test_projection_replays_after_commit_ack_failure_without_duplicate_rows(tmp_path, monkeypatch):
    ledger = PublishLedger(tmp_path / 'ledger.sqlite3')
    run = published(ledger)
    effect = queue_publish_projection(ledger, run.run_id, PublishedProjection('小红书', 'A verified post'))
    acknowledge = ledger.complete_effect

    def interrupted_ack(*_args, **_kwargs):
        raise sqlite3.OperationalError('simulated process interruption after target writes')

    monkeypatch.setattr(ledger, 'complete_effect', interrupted_ack)
    with pytest.raises(sqlite3.OperationalError):
        deliver_publish_projection(ledger, tmp_path, effect)
    monkeypatch.setattr(ledger, 'complete_effect', acknowledge)

    result = drain_publish_projections(ledger, tmp_path)
    calendar, publish_log = rows(tmp_path)
    assert result.applied == (effect.effect_key,)
    assert [item['run_id'] for item in calendar] == [run.run_id]
    assert [item['run_id'] for item in publish_log] == [run.run_id]
    assert ledger.pending_effects(effect_type='publish_projection') == ()


def test_projection_retries_after_one_target_fails(tmp_path, monkeypatch):
    ledger = PublishLedger(tmp_path / 'ledger.sqlite3')
    run = published(ledger)
    effect = queue_publish_projection(ledger, run.run_id, PublishedProjection('小红书', 'A verified post'))
    import easel.publish_projection as projections
    write_log = projections._upsert_publish_log

    def failed_log(*_args, **_kwargs):
        raise OSError('target unavailable')

    monkeypatch.setattr(projections, '_upsert_publish_log', failed_log)
    first = drain_publish_projections(ledger, tmp_path)
    monkeypatch.setattr(projections, '_upsert_publish_log', write_log)

    recovered = drain_publish_projections(ledger, tmp_path)
    calendar, publish_log = rows(tmp_path)
    assert first.pending == (effect.effect_key,)
    assert recovered.applied == (effect.effect_key,)
    assert len(calendar) == len(publish_log) == 1


def test_concurrent_deliveries_keep_one_row_per_run(tmp_path):
    ledger = PublishLedger(tmp_path / 'ledger.sqlite3')
    run = published(ledger)
    effect = queue_publish_projection(ledger, run.run_id, PublishedProjection('小红书', 'A verified post'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _index: deliver_publish_projection(ledger, tmp_path, effect), range(4)))
    calendar, publish_log = rows(tmp_path)
    assert [item['id'] for item in calendar] == [run.run_id]
    assert len(publish_log) == 1


def test_atomic_published_transition_survives_restart_before_queue_call(tmp_path):
    ledger = PublishLedger(tmp_path / 'ledger.sqlite3')
    run = published(ledger)

    restarted = PublishLedger(tmp_path / 'ledger.sqlite3')
    drain_publish_projections(restarted, tmp_path)

    calendar, publish_log = rows(tmp_path)
    assert [item['run_id'] for item in calendar] == [run.run_id]
    assert [item['title'] for item in publish_log] == ['A verified post']


def test_unpublished_run_cannot_create_published_projection(tmp_path):
    ledger = PublishLedger(tmp_path / 'ledger.sqlite3')
    run = ledger.create_run(platform='x', persona='alice', idempotency_key='post',
                            approval_digest='a', media_manifest_hash='m')
    with pytest.raises(ProjectionError):
        queue_publish_projection(ledger, run.run_id, PublishedProjection('X', 'Draft'))
