from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from easel.publish_ledger import PublishLedger
from easel.publish_readback import (ReadbackOutcome, ReadbackQuery, canonical_work_url,
                                    normalize_verification_receipt, verify_readback)


@pytest.fixture
def run(tmp_path):
    ledger = PublishLedger(tmp_path / 'ledger.sqlite3')
    return ledger.create_run(platform='xiaohongshu', persona='alice', idempotency_key='post',
                             approval_digest='a', media_manifest_hash='m', account_id='alice::xiaohongshu',
                             payload={'account_uid': 'actual-platform-account', 'title': 'The current draft'})


def observation(run):
    published_at = (datetime.fromisoformat(run.created_at) + timedelta(seconds=2)).isoformat()
    return {'platform': 'xiaohongshu', 'loggedIn': True, 'account_uid': 'actual-platform-account',
            'notes': [{'id': 'work-1', 'url': 'https://www.xiaohongshu.com/explore/work-1?xsec_token=private',
                       'title': 'The current draft', 'published_at': published_at}]}


def test_verified_readback_uses_current_account_and_exact_work_without_retaining_link_tokens(run):
    evidence = observation(run)
    result = verify_readback(run, evidence, ReadbackQuery(platform_work_id='work-1'))
    assert result.outcome == ReadbackOutcome.VERIFIED
    assert result.account_uid == 'actual-platform-account'
    assert result.platform_work_id == 'work-1'
    assert result.permalink == 'https://www.xiaohongshu.com/explore/work-1'
    assert result.published_at == evidence['notes'][0]['published_at']


@pytest.mark.parametrize('change,reason', [
    ({'account_uid': 'another-account'}, 'account_identity_unverified'),
    ({'account_uid': None}, 'account_identity_unverified'),
    ({'loggedIn': False}, 'account_not_authenticated'),
    ({'platform': 'douyin'}, 'platform_mismatch'),
    ({'notes': [], 'outcome': 'verified'}, 'work_not_observed'),
])
def test_readback_refuses_client_assertion_without_matching_server_evidence(run, change, reason):
    result = verify_readback(run, {**observation(run), **change}, ReadbackQuery(platform_work_id='work-1'))
    assert result.outcome == ReadbackOutcome.UNVERIFIED
    assert result.reason == reason


@pytest.mark.parametrize('change', [
    {'title': 'A different post'},
    {'published_at': '2020-01-01T00:00:00+00:00'},
    {'published_at': None},
    {'author_id': 'another-account'},
    {'status': 'draft'},
    {'status': 'reviewing'},
])
def test_requested_work_cannot_attach_an_unrelated_old_or_unproven_post(run, change):
    payload = observation(run)
    payload['notes'][0].update(change)
    result = verify_readback(run, payload, ReadbackQuery(platform_work_id='work-1'))
    assert result.outcome == ReadbackOutcome.UNVERIFIED


def test_missing_publication_account_identity_keeps_legacy_run_unverified(run):
    legacy = replace(run, payload={'title': 'The current draft'})
    result = verify_readback(legacy, observation(run), ReadbackQuery(platform_work_id='work-1'))
    assert result.outcome == ReadbackOutcome.UNVERIFIED


def test_different_exact_matches_are_ambiguous(run):
    payload = observation(run)
    payload['notes'].append({**payload['notes'][0], 'url': 'https://www.xiaohongshu.com/explore/another-link'})
    result = verify_readback(run, payload, ReadbackQuery(platform_work_id='work-1'))
    assert result.outcome == ReadbackOutcome.AMBIGUOUS


def test_existing_browser_receipt_is_verified_with_actual_account_and_work_timestamp(run):
    published = datetime.fromisoformat(run.created_at) + timedelta(seconds=2)
    receipt = {'outcome': 'verified', 'matched': {'platform_content_id': 'work-1',
                'title': 'The current draft', 'status': 'published',
                'published_at_ms': int(published.timestamp() * 1000)},
               'evidence': {'account': {'platform_account_id': 'actual-platform-account'}}}
    evidence = normalize_verification_receipt(run.platform, receipt)
    result = verify_readback(run, evidence, ReadbackQuery(platform_work_id='work-1'))
    assert result.outcome == ReadbackOutcome.VERIFIED
    assert result.account_uid == 'actual-platform-account'


@pytest.mark.parametrize('url', [
    'https://private.example/explore/work-1',
    'https://user:password@www.xiaohongshu.com/explore/work-1',
    'https://www.xiaohongshu.com:invalid/explore/work-1',
    'https://[invalid/explore/work-1',
])
def test_work_links_reject_untrusted_or_malformed_origins(url):
    assert canonical_work_url('xiaohongshu', url) == ''
