"""Native adapters execute through the reviewed frozen-media ledger API."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest

from easel.channel_publish_router import create_router
from easel.channels import ChannelAdapter, Credential, CredentialStore
from easel.channels.models import AccountProfile


class NativeServer:
    """Mutable native platform state tracks uploaded bytes, submissions, and app completion."""
    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.uploads: list[bytes] = []
        self.covers: list[bytes] = []
        self.posts: list[bytes] = []
        self.share_jobs = 0
        self.ready = False
        self.actor = 'actor'

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith('/user/account/info'): return httpx.Response(200, json={'code': 0, 'data': {'openid': 'actor', 'name': 'Bili'}})
        if path.endswith('/data/user/stat'): return httpx.Response(200, json={'code': 0, 'data': {'follower': 1}})
        if path.endswith('/video/init'): return httpx.Response(200, json={'code': 0, 'data': {'upload_token': 'video-token'}})
        if path.endswith('/part/upload') or path.endswith('/fragment'):
            self.uploads.append(request.read())
            return httpx.Response(200, json={'code': 0, 'result': 1})
        if path.endswith('/video/complete') or path.endswith('/upload/complete'): return httpx.Response(200, json={'code': 0, 'result': 1})
        if path.endswith('/cover/upload'):
            self.covers.append(request.read())
            return httpx.Response(200, json={'code': 0, 'data': {'url': 'https://i0.hdslb.com/cover.jpg'}})
        if path.endswith('/archive/add-by-utoken'):
            assert self.uploads == [b'approved-video']
            self.posts.append(request.read())
            return httpx.Response(200, json={'code': 0, 'data': {'resource_id': 'BV17ABC'}})
        if path.endswith('/archive/view'):
            return httpx.Response(200, json={'code': 0, 'data': {'resource_id': 'BV17ABC', 'title': 'Reviewed title', 'desc': 'Body', 'addit_info': {'state': 0 if self.ready else -1}}})
        if path.endswith('/user_info'): return httpx.Response(200, json={'result': 1, 'user_info': {'name': 'Kwai'}})
        if path.endswith('/start_upload'):
            return httpx.Response(200, json={'result': 1, 'upload_token': 'video-token', 'endpoint': 'upload.kuaishou.com'})
        if path.endswith('/photo/publish'):
            assert self.uploads == [b'approved-video']
            self.covers.append(request.read())
            self.posts.append(request.content)
            return httpx.Response(200, json={'result': 1, 'video_info': {'photo_id': 'photo-1'}})
        if path.endswith('/photo/info'):
            return httpx.Response(200, json={'result': 1, 'video_info': {'photo_id': 'photo-1', 'pending': not self.ready, 'caption': 'Reviewed title'}})
        if path.endswith('/oauth/userinfo/'):
            return httpx.Response(200, json={'data': {'open_id': 'actor', 'nickname': 'Douyin'}})
        if path.endswith('/oauth/client_token/'):
            return httpx.Response(200, json={'data': {'access_token': 'app-token', 'expires_in': 7200}})
        if path.endswith('/open/getticket/'):
            return httpx.Response(200, json={'data': {'ticket': 'open-ticket', 'expires_in': 7200}})
        if path.endswith('/share-id/'):
            if request.url.params.get('share_id'):
                return httpx.Response(200, json={'data': {'share_id': 'share-1', 'video_id': 'video-1' if self.ready else '',
                    'open_id': self.actor, 'share_url': 'https://www.douyin.com/video/video-1'}})
            self.share_jobs += 1
            return httpx.Response(200, json={'data': {'share_id': 'share-1'}})
        pytest.fail(f'unexpected native endpoint {request.method} {request.url}')


def native_client(root: Path, platform: str, server: NativeServer) -> TestClient:
    project = root / 'project'
    project.mkdir()
    (project / '.easel.json').write_text('{"profile":"alice"}')
    (project / 'video.mp4').write_bytes(b'approved-video')
    (project / 'cover.jpg').write_bytes(b'approved-cover')
    store = CredentialStore(root / '_channels')
    store.save(Credential(persona='alice', platform=platform, account_uid='actor', access_token=SecretStr('user-token')))
    prefix = 'EASEL_' + platform.upper().replace('-', '_') + '_'
    settings = {prefix + 'CLIENT_ID': 'app', prefix + 'CLIENT_SECRET': 'app-secret', 'EASEL_CHANNEL_MEDIA_BASE_URL': 'https://media.example'}
    app = FastAPI()
    app.include_router(create_router(lambda: root, lambda: settings, lambda p: p == 'alice', lambda request: None,
        adapter_factory=lambda name: ChannelAdapter(name, settings=settings, store=store, transport=httpx.MockTransport(server.handle))))
    return TestClient(app)


@pytest.mark.parametrize('platform', ['bilibili-open', 'kwai-open'])
def test_native_upload_review_pipeline_reuses_real_frozen_media_and_cover(tmp_path: Path, platform: str):
    server = NativeServer(platform)
    client = native_client(tmp_path, platform, server)
    content = {'persona': 'alice', 'account_uid': 'actor', 'title': 'Reviewed title', 'body': 'Body #topic',
        'media': [{'kind': 'video', 'path': 'project/video.mp4'}], 'cover_path': 'project/cover.jpg',
        'options': {'tid': 17} if platform == 'bilibili-open' else {}}
    checked = client.post(f'/api/channel-publish/{platform}/precheck', json=content)
    assert checked.status_code == 200, checked.text
    approved = checked.json()
    payload = {**content, 'reviewed': True, 'idempotency_key': 'native-1',
        **{key: approved[key] for key in ('approval_digest', 'approval_expires_at')}}
    response = client.post(f'/api/channel-publish/{platform}/publish', json=payload)
    assert response.status_code == 200 and response.json()['state'] == 'submitted', response.text
    assert client.post(f'/api/channel-publish/{platform}/publish', json=payload).json()['run_id'] == response.json()['run_id']
    server.ready = True
    complete = client.post(f"/api/channel-publish/{platform}/{response.json()['run_id']}/finalize", json={'persona': 'alice', 'account_uid': 'actor'})
    assert complete.json()['state'] == 'published' and complete.json()['cover_status'] == 'applied', complete.text
    assert len(server.posts) == len(server.covers) == 1
    assert b'approved-cover' in server.covers[0] and server.uploads == [b'approved-video']


@pytest.mark.parametrize('actor', ['actor', 'other-account'])
def test_douyin_app_handoff_requires_authoritative_completion_for_original_actor(tmp_path: Path, actor: str):
    server = NativeServer('douyin-open')
    client = native_client(tmp_path, 'douyin-open', server)
    content = {'persona': 'alice', 'account_uid': 'actor', 'title': 'Reviewed title', 'body': 'Body',
        'media': [{'kind': 'video', 'path': 'project/video.mp4'}]}
    approval = client.post('/api/channel-publish/douyin-open/precheck', json=content)
    assert approval.status_code == 200, approval.text
    approved = approval.json()
    value = client.post('/api/channel-publish/douyin-open/publish', json={**content, 'reviewed': True, 'idempotency_key': 'share-1',
        **{key: approved[key] for key in ('approval_digest', 'approval_expires_at')}}).json()
    assert value['state'] == 'submitted' and value['requires_user_action']
    assert value['action_url'].startswith('snssdk1128://openplatform/share?')
    endpoint = f"/api/channel-publish/douyin-open/{value['run_id']}/finalize"
    scope = {'persona': 'alice', 'account_uid': 'actor'}
    assert client.post(endpoint, json=scope).json()['state'] == 'submitted'
    server.ready, server.actor = True, actor
    completed = client.post(endpoint, json=scope).json()
    assert completed['state'] == ('published' if actor == 'actor' else 'submitted')
    assert server.share_jobs == 1 and server.uploads == []


def test_wechat_article_uses_publisher_app_identity_and_durable_draft_checkpoint(tmp_path: Path):
    drafts, submits, covers = [], [], []
    class ArticleServer(NativeServer):
        def handle(self, request):
            import json
            path = request.url.path
            if path.endswith('/sns/userinfo'): return httpx.Response(200, json={'openid': 'visiting-user'})
            if path.endswith('/cgi-bin/token'): return httpx.Response(200, json={'access_token': 'publisher-app-token', 'expires_in': 7200})
            if path.endswith('/material/add_material'):
                covers.append(request.read())
                return httpx.Response(200, json={'media_id': 'owned-thumb'})
            if path.endswith('/draft/add'):
                drafts.append(json.loads(request.content))
                return httpx.Response(200, json={'media_id': 'draft-1'})
            if path.endswith('/freepublish/submit'):
                submits.append(json.loads(request.content)['media_id'])
                return httpx.Response(200, json={'publish_id': 'publish-1'})
            if path.endswith('/freepublish/get'):
                return httpx.Response(200, json={'publish_status': 0 if self.ready else 1, 'article_id': 'article-1',
                    'article_detail': {'item': [{'article_url': 'https://mp.weixin.qq.com/s/article-1'}]}})
            return super().handle(request)
    server = ArticleServer('wechat-official-open')
    client = native_client(tmp_path, 'wechat-official-open', server)
    store = CredentialStore(tmp_path / '_channels')
    store.save(Credential(persona='alice', platform='wechat-official-open', account_uid='appid:app', parent_uid='visiting-user',
        access_token=SecretStr('sns-user-token'), profile=AccountProfile(account_uid='appid:app', extra={'client_id': 'app'})))
    content = {'persona': 'alice', 'account_uid': 'appid:app', 'title': 'Reviewed article', 'body': '<p>Article body</p>',
        'cover_path': 'project/cover.jpg'}
    checked = client.post('/api/channel-publish/wechat-official-open/precheck', json=content)
    assert checked.status_code == 200, checked.text
    approved = checked.json()
    response = client.post('/api/channel-publish/wechat-official-open/publish', json={**content, 'reviewed': True, 'idempotency_key': 'article-1',
        **{key: approved[key] for key in ('approval_digest', 'approval_expires_at')}})
    assert response.status_code == 200 and response.json()['state'] == 'submitted', response.text
    assert len(drafts) == len(covers) == 1 and submits == []
    assert drafts[0]['articles'][0]['thumb_media_id'] == 'owned-thumb'
    value = response.json()
    endpoint = f"/api/channel-publish/wechat-official-open/{value['run_id']}/finalize"
    scope = {'persona': 'alice', 'account_uid': 'appid:app'}
    assert client.post(endpoint, json=scope).json()['state'] == 'submitted'
    server.ready = True
    completed = client.post(endpoint, json=scope).json()
    assert completed['state'] == 'published' and completed['account_uid'] == 'appid:app'
    assert completed['platform_work_id'] == 'article-1' and submits == ['draft-1']
    assert b'approved-cover' in covers[0]


@pytest.mark.parametrize('evidence', ['matching', 'old', 'different_content'])
def test_wechat_candidate_link_requires_actual_content_and_time_proof(tmp_path: Path, evidence: str):
    class ChannelsServer(NativeServer):
        def handle(self, request):
            path = request.url.path
            if path.endswith('/auth/auth_data'):
                return httpx.Response(200, json={'errCode': 0, 'data': {'finderUser': {'uniqId': 'finder-user', 'finderUsername': 'finder-id'}}})
            if path.endswith('/cgi-bin/token'):
                return httpx.Response(200, json={'access_token': 'app-token', 'expires_in': 7200})
            if path.endswith('/finder/link/info'):
                return httpx.Response(200, json={'data': {'finder_id': 'finder-id', 'video_id': 'video-1'}})
            if path.endswith('/finder/video/get'):
                return httpx.Response(200, json={'data': {'finder_id': 'finder-id',
                    'description': 'unrelated' if evidence == 'different_content' else 'Reviewed title\nBody',
                    'publish_time': '2000-01-01T00:00:00Z' if evidence == 'old' else datetime.now(timezone.utc).isoformat()}})
            return super().handle(request)
    server = ChannelsServer('wechat-channels-open')
    client = native_client(tmp_path, 'wechat-channels-open', server)
    CredentialStore(tmp_path / '_channels').save(Credential(persona='alice', platform='wechat-channels-open', account_uid='finder-user',
        access_token=SecretStr('sessionid=session;wxuin=1'), profile=AccountProfile(account_uid='finder-user', extra={'client_id': 'app', 'finder_id': 'finder-id'})))
    content = {'persona': 'alice', 'account_uid': 'finder-user', 'title': 'Reviewed title', 'body': 'Body',
        'media': [{'kind': 'video', 'path': 'project/video.mp4'}], 'options': {'workLink': 'https://channels.weixin.qq.com/platform/work'}}
    checked = client.post('/api/channel-publish/wechat-channels-open/precheck', json=content)
    assert checked.status_code == 200, checked.text
    approval = checked.json()
    response = client.post('/api/channel-publish/wechat-channels-open/publish', json={**content, 'reviewed': True, 'idempotency_key': 'owned-link',
        **{key: approval[key] for key in ('approval_digest', 'approval_expires_at')}})
    assert response.status_code == 200 and response.json()['state'] == 'submitted', response.text
    value = client.post(f"/api/channel-publish/wechat-channels-open/{response.json()['run_id']}/finalize",
        json={'persona': 'alice', 'account_uid': 'finder-user'}).json()
    assert value['state'] == ('published' if evidence == 'matching' else 'submitted')
    assert value['requires_user_action'] is (evidence != 'matching')
