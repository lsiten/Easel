"""Native callbacks require signatures, recorded IDs, and owned deferred delivery."""
import hashlib
import hmac
import json

import pytest

from easel.channels import CredentialStore
from easel.channels.completion_events import load_verified_events
from easel.channels.native_webhooks import load_native_webhook_events, process_pending_app_events, save_native_event, verify_native


def test_native_signature_variants_and_xml_challenge():
    body = b'{"event":"create_video","content":{"share_id":"share","video_id":"123"}}'
    config = {'EASEL_DOUYIN_OPEN_CLIENT_ID': 'app', 'EASEL_DOUYIN_OPEN_CLIENT_SECRET': 'secret'}
    for signature in (hashlib.sha1(b'secret' + body).hexdigest(), hmac.new(b'secret', body, hashlib.sha256).hexdigest()):
        assert verify_native('douyin-open', body, {'x-douyin-signature': signature}, {}, config)
        assert not verify_native('douyin-open', body + b' ', {'x-douyin-signature': signature}, {}, config)
    bili = {**config, 'EASEL_BILIBILI_OPEN_CLIENT_ID': 'app', 'EASEL_BILIBILI_OPEN_CLIENT_SECRET': 'secret'}
    headers = {'x-bili-accesskeyid': 'app', 'x-bili-content-md5': hashlib.md5(body).hexdigest(),
        'x-bili-signature-method': 'HMAC-SHA256', 'x-bili-signature-nonce': 'nonce', 'x-bili-signature-version': '2.0', 'x-bili-timestamp': '123'}
    headers['Authorization'] = hmac.new(b'secret', '\n'.join(key + ':' + value for key, value in headers.items()).encode(), hashlib.sha256).hexdigest()
    assert verify_native('bilibili-open', body, headers, {}, bili)
    assert not verify_native('bilibili-open', body + b' ', headers, {}, bili)
    signature = hashlib.sha1(''.join(sorted(['token', '123', 'nonce'])).encode()).hexdigest()
    wx = {'EASEL_WECHAT_OFFICIAL_OPEN_WEBHOOK_VERIFY_TOKEN': 'token'}
    assert verify_native('wechat-official-open', b'', {}, {'signature': signature, 'timestamp': '123', 'nonce': 'nonce'}, wx)


def test_unbound_callback_waits_for_recorded_receipt_then_scopes_delivery(tmp_path):
    store = CredentialStore(tmp_path)
    config = {'EASEL_DOUYIN_OPEN_CLIENT_ID': 'app', 'EASEL_DOUYIN_OPEN_CLIENT_SECRET': 'secret'}
    body = json.dumps({'event': 'create_video', 'content': {'share_id': 'share', 'video_id': '123'}, 'open_id': 'owner'}).encode()
    saved = save_native_event(store, 'douyin-open', body, config)
    assert b'"share"' not in store.path.read_bytes()
    assert process_pending_app_events(store, config, lambda *_: []) == 0
    assert load_native_webhook_events(store) == []
    with store.connection() as db: db.execute('UPDATE native_webhook_inbox SET next_attempt=0')
    calls = []
    def resolver(platform, ids):
        calls.append((platform, ids))
        return [('alice', 'owner'), ('bob', 'foreign')]
    assert process_pending_app_events(store, config, resolver) == 1
    events = load_verified_events(store)
    assert len(events) == 1 and events[0]['persona'] == 'alice'
    assert events[0]['events'][0]['external_request_id'] == 'share'
    assert events[0]['events'][0]['platform_work_id'] == '123'
    assert calls == [('douyin-open', ['share', '123'])]
    assert load_native_webhook_events(store) == []


def test_unbound_oldest_due_and_quarantine_does_not_drop_later_callback(tmp_path):
    store = CredentialStore(tmp_path)
    config = {'EASEL_BILIBILI_OPEN_CLIENT_ID': 'app'}
    for index in range(105):
        body = json.dumps({'event': 'video_open', 'content': {'resource_id': f'BV{index}'}}).encode()
        save_native_event(store, 'bilibili-open', body, config)
    with store.connection() as db:
        first = db.execute('SELECT digest FROM native_webhook_inbox ORDER BY received_at LIMIT 1').fetchone()['digest']
        db.execute('UPDATE native_webhook_inbox SET encrypted=? WHERE digest=?', (b'bad', first))
    batch = load_native_webhook_events(store, limit=100)
    assert len(batch) == 99 and batch[0]['candidate_ids'] == ['BV1']
    assert process_pending_app_events(store, config, lambda *_: [], limit=100) == 0
    remaining = load_native_webhook_events(store)
    assert len(remaining) == 4 and remaining[0]['candidate_ids'] == ['BV101']
