"""Scoped, reviewed handoff grants with server-owned native share receipts."""
from collections.abc import Callable, Mapping
from dataclasses import asdict
import hashlib
import hmac
from pathlib import Path
import time
from urllib.parse import urlencode

import httpx
from pydantic import TypeAdapter

from easel._channel_publish_media import approval_signature, frozen_paths, media_base
from easel._channel_publish_worker import lease_heartbeat
from easel.channels import ChannelError, CredentialStore
from easel.channels.douyin_open import DouyinOpen
from easel.channels.models import JsonDocument
from easel.channels.native_config import native_setting
from easel.native_handoff_models import HandoffPlatform, HandoffStart, HandoffTemplate
from easel.native_handoff_validation import validate_template
from easel.native_topics import strip_topics, topic_insertions
from easel.ownership import MediaManifestEntry, build_media_manifest, freeze_media
from easel.publish_context import validate_context
from easel.publish_ledger import PublishLedger
from easel.publish_models import PublishRun, RunState, digest_mapping
from easel.publish_projection import drain_publish_projections
from easel.publish_recovery import worker_owner
from easel.rednote_share import share_config

_MANIFEST = TypeAdapter(tuple[MediaManifestEntry, ...])


class NativeHandoff:
    def __init__(self, root: Path, settings: Callable[[], Mapping[str, str]], *, transport: httpx.BaseTransport | None = None) -> None:
        self.root, self.settings, self.transport = root, settings, transport
        self.store = CredentialStore(root / '_channels')
        self.ledger = PublishLedger(root / '_publish/ledger.sqlite3')

    def _grant(self, run: PublishRun) -> str:
        return approval_signature(self.root, f"native-template:{run.run_id}:{run.persona}:{run.approval_digest}:{run.payload['expires_at']}")

    def create(self, platform: HandoffPlatform, request: HandoffTemplate) -> JsonDocument:
        if not request.reviewed:
            raise ChannelError(platform, 'explicit_review_required', status=403)
        payload = request.model_dump(mode='json')
        existing = next((run for run in self.ledger.list_runs(persona=request.persona, platform='native-template:' + platform)
                         if run.idempotency_key == request.request_key), None)
        if existing is not None:
            if existing.payload['native_template'] != payload:
                raise ChannelError(platform, 'idempotency_conflict', status=409)
            if existing.payload.get('native_ready'):
                self.template(existing.run_id, self._grant(existing))
                return self.owner_view(existing)
        validate_context(self.root, request.persona, request.context)
        manifest = build_media_manifest(self.root, request.persona, [item.path for item in request.media])
        validate_template(self.root, platform, request, manifest)
        run = self.ledger.create_run(platform='native-template:' + platform, persona=request.persona,
            account_id=request.persona, idempotency_key=request.request_key,
            approval_digest=digest_mapping(payload), media_manifest_hash=digest_mapping({'media': [asdict(item) for item in manifest]}),
            payload={'native_template': payload, 'native_platform': platform, 'title': request.title,
                     'native_manifest': [asdict(item) for item in manifest], 'context': request.context.model_dump(mode='json'),
                     'expires_at': int(time.time()) + request.expires_in}, initial_state=RunState.READY_FOR_MANUAL_SUBMISSION)
        if run.state != RunState.READY_FOR_MANUAL_SUBMISSION:
            raise ChannelError(platform, 'template_revoked', status=410)
        if not run.payload.get('native_ready'):
            lease = self.ledger.acquire_lease(run.run_id, worker_owner())
            try:
                with lease_heartbeat(self.ledger, lease):
                    freeze_media(self.root, run.persona, run.run_id, manifest)
                    run = self.ledger.checkpoint_run(run.run_id, expected=run.state, fencing_token=lease.fencing_token,
                        payload={'native_ready': True})
            finally:
                self.ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)
        return self.owner_view(run)

    def owner_view(self, run: PublishRun) -> JsonDocument:
        token = self._grant(run)
        path = '/?' + urlencode({'handoff': run.run_id, 'token': token})
        base = media_base(self.settings()) if self.settings().get('EASEL_CHANNEL_MEDIA_BASE_URL') else ''
        return {'template_id': run.run_id, 'persona': run.persona, 'platform': run.payload['native_platform'],
                'state': run.state.value, 'expires_at': run.payload['expires_at'],
                'path': path, 'public_url': base + path if base else '', 'token': token}

    def owned(self, persona: str, template_id: str) -> PublishRun:
        run = self.ledger.get_run(template_id)
        if run is None or run.persona != persona or 'native_template' not in run.payload:
            raise ChannelError('handoff', 'template_not_found', status=404)
        return run

    def template(self, template_id: str, token: str) -> PublishRun:
        run = self.ledger.get_run(template_id)
        if run is None or not run.payload.get('native_ready') or not hmac.compare_digest(token.encode(), self._grant(run).encode()):
            raise ChannelError('handoff', 'template_not_found', status=404)
        if run.state != RunState.READY_FOR_MANUAL_SUBMISSION or int(run.payload['expires_at']) <= time.time():
            raise ChannelError('handoff', 'template_expired', status=410)
        return run

    def public_view(self, template_id: str, token: str) -> JsonDocument:
        run = self.template(template_id, token)
        content = HandoffTemplate.model_validate(run.payload['native_template'])
        return {'template_id': template_id, 'platform': run.payload['native_platform'], 'title': content.title,
                'body': content.body, 'expires_at': run.payload['expires_at'],
                'media': [{'kind': media.kind, 'path': f'/api/native-handoff/templates/{template_id}/media/{index}?' + urlencode({'token': token})}
                          for index, media in enumerate(content.media)]}

    def media(self, template_id: str, index: int, token: str) -> Path:
        run = self.template(template_id, token)
        manifest = _MANIFEST.validate_python(run.payload['native_manifest'])
        if not 0 <= index < len(manifest):
            raise ChannelError('handoff', 'media_not_found', status=404)
        return frozen_paths(self.root, run.persona, template_id, manifest)[index]

    def start(self, template_id: str, token: str, request: HandoffStart) -> JsonDocument:
        template = self.template(template_id, token)
        if not request.reviewed:
            raise ChannelError('handoff', 'explicit_review_required', status=403)
        with self.ledger._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS native_handoff_attempts(template_id TEXT NOT NULL,created_at REAL NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM native_handoff_attempts WHERE created_at<?', (time.time() - 60,))
            count = db.execute('SELECT COUNT(*) FROM native_handoff_attempts WHERE template_id=?', (template_id,)).fetchone()[0]
            if count >= 60:
                raise ChannelError('handoff', 'rate_limit_exceeded', status=429)
            db.execute('INSERT INTO native_handoff_attempts VALUES(?,?)', (template_id, time.time()))
        content = HandoffTemplate.model_validate(template.payload['native_template'])
        platform = str(template.payload['native_platform'])
        disabled = self.settings().get('EASEL_PUBLISH_DISABLED_PLATFORMS', '').split(',')
        if platform in {item.strip() for item in disabled}:
            raise ChannelError(platform, 'publishing_disabled', status=403)
        if platform == 'rednote-link':
            return {'state': 'ready_for_manual_submission', **share_config(self.settings(), transport=self.transport),
                    'share_info': self.public_view(template_id, token), 'sdk_method': 'xhs.share'}
        identity = hashlib.sha256((template_id + ':' + request.request_key).encode()).hexdigest()
        run = self.ledger.create_run(platform='douyin-open', persona=template.persona,
            account_id='app-handoff:' + identity, idempotency_key=identity,
            approval_digest=template.approval_digest, media_manifest_hash=template.media_manifest_hash,
            payload={'title': content.title, 'body': content.body, 'auth_mode': 'app_handoff',
                     'context': content.context.model_dump(mode='json'), 'handoff_template': template_id,
                     'app_client_id': native_setting(self.settings(), platform, 'CLIENT_ID')},
            initial_state=RunState.READY_FOR_MANUAL_SUBMISSION)
        if run.state != RunState.READY_FOR_MANUAL_SUBMISSION:
            return self.action(run)
        lease = self.ledger.acquire_lease(run.run_id, worker_owner())
        try:
            with lease_heartbeat(self.ledger, lease):
                current = self.ledger.get_run(run.run_id)
                if current is not None and current.state != RunState.READY_FOR_MANUAL_SUBMISSION:
                    return self.action(current)
                base = media_base(self.settings())
                urls = [base + f'/api/native-handoff/templates/{template_id}/media/{index}?' + urlencode({'token': token}) for index in range(len(content.media))]
                app = DouyinOpen(self.settings(), transport=self.transport, store=self.store)
                options = {**content.options, 'short_title': content.options.get('short_title', content.title),
                           'title_hashtag_list': topic_insertions(content.body)}
                generated = app.generate_handoff(title=strip_topics(content.body) or content.title,
                    video_url=urls[0] if content.media[0].kind == 'video' else '',
                    image_urls=urls if content.media[0].kind == 'image' else (), options=options)
                action = {'scheme': generated['schema'], 'share_id': generated['share_id'],
                          'expires_at': min(float(generated['expires_at']), float(template.payload['expires_at']))}
                from easel.local_access import AccessStore
                code = AccessStore(self.root / '_access').create_handoff_link(template.persona, str(generated['schema']), float(action['expires_at']))
                action['short_link'] = base + '/s/' + code
                run = self.ledger.checkpoint_run(run.run_id, expected=run.state, target=RunState.SUBMITTED,
                    fencing_token=lease.fencing_token, external_request_id=str(generated['share_id']),
                    payload={'user_action': action})
        finally:
            self.ledger.release_lease(run.run_id, lease.owner, lease.fencing_token)
        return self.action(run)

    @staticmethod
    def action(run: PublishRun) -> JsonDocument:
        value = run.payload.get('user_action') or {}
        return {'run_id': run.run_id, 'state': run.state.value, 'auth_mode': 'app_handoff',
                'publisher_identity': 'selected_in_app', 'platform_work_id': run.platform_work_id,
                'permalink': run.permalink, 'user_action': value}

    def status(self, template_id: str, token: str, run_id: str) -> JsonDocument:
        template = self.template(template_id, token)
        run = self.ledger.get_run(run_id)
        if run is None or run.persona != template.persona or run.payload.get('handoff_template') != template_id:
            raise ChannelError('handoff', 'run_not_found', status=404)
        return self.poll_owned(template.persona, run_id)

    def poll_owned(self, persona: str, run_id: str) -> JsonDocument:
        run = self.ledger.get_run(run_id)
        if run is None or run.persona != persona or not run.payload.get('handoff_template'):
            raise ChannelError('handoff', 'run_not_found', status=404)
        if run.state != RunState.SUBMITTED:
            return self.action(run)
        if run.payload.get('app_client_id') != native_setting(self.settings(), run.platform, 'CLIENT_ID'):
            raise ChannelError(run.platform, 'application_configuration_changed', status=409)
        lease = self.ledger.acquire_lease(run_id, worker_owner())
        try:
            current = self.ledger.get_run(run_id)
            if current is not None and current.state != RunState.SUBMITTED:
                return self.action(current)
            result = DouyinOpen(self.settings(), transport=self.transport, store=self.store).get_share_publish_result(run.external_request_id)
            video = result.get('video_id')
            if isinstance(video, (str, int)) and str(video):
                run = self.ledger.checkpoint_run(run_id, expected=run.state, target=RunState.PUBLISHED,
                    fencing_token=lease.fencing_token, platform_work_id=str(video),
                    permalink='https://www.douyin.com/video/' + str(video),
                    payload={'publisher_account_uid': result.get('open_id') or '', 'handoff_verified': True})
        finally:
            self.ledger.release_lease(run_id, lease.owner, lease.fencing_token)
        drain_publish_projections(self.ledger, self.root)
        return self.action(run)
