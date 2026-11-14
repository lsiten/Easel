"""Adapt authenticated official-provider work data to the shared evidence verifier."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from easel._channel_publish_models import ChannelIntent, ChannelReconcileRequest
from easel.channels import ChannelAdapter, Credential
from easel.channels.models import JsonDocument
from easel.channels.reads import _document, _rows
from easel.publish_models import PublishRun
from easel.publish_readback import (ReadbackOutcome, ReadbackQuery, ReadbackResult,
    canonical_work_url, verify_readback)


def _text(row: JsonDocument, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if isinstance(value, str):
            return value
    return ''


def _rows_from_work(payload: JsonDocument) -> list[JsonDocument]:
    nested = _document(payload.get('data'))
    rows = _rows(nested.get('videos')) or _rows(payload.get('data')) or _rows(payload.get('items'))
    return rows or [nested if nested.get('id') else payload]


def official_readback(adapter: ChannelAdapter, credential: Credential,
                      run: PublishRun, request: ChannelReconcileRequest) -> ReadbackResult:
    """Require real account identity and content/time corroboration for a supplied candidate."""
    actual = adapter.profile(credential).account_uid
    if actual != run.account_id:
        return ReadbackResult(ReadbackOutcome.UNVERIFIED, actual, reason='authenticated_account_mismatch')
    work_id = request.platform_work_id or run.platform_work_id
    payload = adapter.work(credential, work_id) if work_id else adapter.works(credential)
    intent = ChannelIntent.model_validate(run.payload['channel_intent'])
    expected_text = intent.title if run.platform in {'youtube', 'pinterest', 'bilibili-open', 'wechat-official-open'} else intent.body or intent.title
    if run.platform in {'kwai-open', 'douyin-open', 'wechat-channels-open'}:
        expected_text = '\n'.join(value for value in (intent.title, intent.body) if value)
    override = intent.options.get('caption' if run.platform == 'instagram' else 'commentary' if run.platform == 'linkedin' else '')
    if isinstance(override, str):
        expected_text = override
    notes: list[JsonDocument] = []
    for row in _rows_from_work(payload):
        snippet, identifier = _document(row.get('snippet')), row.get('id')
        identifier_doc = _document(identifier)
        identity = str(identifier_doc.get('videoId') or identifier or '')
        title = _text(snippet, 'title') or _text(row, 'caption', 'text', 'message', 'title')
        published = next((row.get(key) for key in ('timestamp', 'created_time', 'created_at', 'create_time') if row.get(key) is not None), snippet.get('publishedAt'))
        url = _text(row, 'permalink', 'permalink_url', 'share_url', 'url')
        if run.platform == 'youtube' and identity:
            url = f'https://www.youtube.com/watch?v={identity}'
        if run.platform == 'twitter' and identity:
            url = f'https://x.com/i/status/{identity}'
        author = _text(row, 'author_id') or _text(_document(row.get('from')), 'id') or _text(snippet, 'channelId')
        status = _text(row, 'status')
        privacy = _text(_document(row.get('status')), 'privacyStatus')
        if run.platform == 'youtube':
            approved_privacy = intent.options.get('privacyStatus', intent.options.get('privacy_status'))
            schedule = intent.options.get('publishAt')
            scheduled = isinstance(schedule, str) and datetime.fromisoformat(schedule.replace('Z', '+00:00')) > datetime.now(timezone.utc)
            if scheduled:
                status = 'scheduled'
            elif privacy == 'private' and approved_privacy == 'private':
                privacy, status = '', 'published'
        notes.append({'id': identity, 'title': title, 'published_at': published,
            'status': status if status == 'scheduled' else privacy if privacy == 'private' else status, 'account_uid': author or actual,
            'url': canonical_work_url(run.platform, url)})
    evidence: JsonDocument = {'platform': run.platform, 'loggedIn': True, 'account_uid': actual, 'notes': notes}
    verified_run = replace(run, payload={**run.payload, 'title': expected_text})
    return verify_readback(verified_run, evidence, ReadbackQuery(request.platform_work_id, request.permalink))
