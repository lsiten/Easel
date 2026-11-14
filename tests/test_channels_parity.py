"""Source-declared platform options, native uploads, and pagination contracts."""
import json

import httpx
import pytest
from pydantic import SecretStr

from easel.channels import ChannelAdapter, ChannelError, Credential, PublishContent, PublishReceipt
from easel.channels.models import MediaInput, MediaMetadata
from easel.channels.twitter_text import weighted_length


def account(platform):
    return Credential(persona='alice', platform=platform, account_uid='actor', parent_uid='parent', access_token=SecretStr('token'))


def test_youtube_category_is_validated_before_remote_creation_and_full_update_preserves_state():
    calls, assignable = [], [False]
    def provider(request):
        calls.append(request.method)
        if request.url.path.endswith('/videoCategories'):
            assert request.url.params['id'] == '22'
            return httpx.Response(200, json={'items': [{'id': '22', 'snippet': {'assignable': assignable[0]}}]})
        if request.method == 'GET':
            return httpx.Response(200, json={'items': [{'id': 'work', 'snippet': {'channelId': 'actor', 'categoryId': '22'}, 'status': {'privacyStatus': 'unlisted'}}]})
        body = json.loads(request.content)
        assert body == {'id': 'work', 'snippet': {'categoryId': '22', 'title': 'Title', 'description': 'Body', 'tags': ['AI']},
            'status': {'privacyStatus': 'private', 'license': 'creativeCommon', 'embeddable': False}}
        return httpx.Response(200, json=body)
    adapter = ChannelAdapter('youtube', transport=httpx.MockTransport(provider))
    content = PublishContent(title='Title', body='Body #AI', media=(MediaInput(kind='video'),), options={'categoryId': '22', 'content_length': 10})
    with pytest.raises(ChannelError, match='category_not_assignable'): adapter.publish(account('youtube'), content)
    assert calls == ['GET']
    assignable[0] = True
    adapter.update(account('youtube'), 'work', PublishContent(title='Title', body='Body #AI', options={
        'categoryId': '22', 'privacyStatus': 'private', 'license': 'creativeCommon', 'embeddable': False}))
    assert calls[-1] == 'PUT'


def test_facebook_pages_and_pinterest_boards_follow_only_opaque_cursors():
    def provider(request):
        if request.url.host == 'graph.facebook.com':
            if request.url.params.get('after') == 'next':
                return httpx.Response(200, json={'data': [{'id': 'second', 'name': 'Second', 'access_token': 'page-token'}]})
            return httpx.Response(200, json={'data': [{'id': 'first', 'name': 'First', 'access_token': 'one'}],
                'paging': {'next': 'https://attacker.example/ignored', 'cursors': {'after': 'next'}}})
        if request.url.params.get('bookmark') == 'next': return httpx.Response(200, json={'items': [{'id': 'board-2'}]})
        return httpx.Response(200, json={'items': [{'id': 'board-1'}], 'bookmark': 'next'})
    facebook = ChannelAdapter('facebook', transport=httpx.MockTransport(provider))
    assert [value.account_uid for value in facebook.accounts(account('facebook'))] == ['first', 'second']
    selected = facebook.select_account(account('facebook'), 'second')
    assert selected.parent_uid == 'actor' and selected.access_token.get_secret_value() == 'page-token'
    pinterest = ChannelAdapter('pinterest', transport=httpx.MockTransport(provider))
    assert pinterest.options(account('pinterest'))['items'] == [{'id': 'board-1'}, {'id': 'board-2'}]


def test_instagram_failed_second_child_compensates_only_created_container():
    calls = []
    def provider(request):
        calls.append((request.method, request.url.path))
        if request.method == 'DELETE': return httpx.Response(200, json={'success': True})
        if len(calls) == 1: return httpx.Response(200, json={'id': 'child-1'})
        return httpx.Response(400, json={'error': {'code': 100}})
    adapter = ChannelAdapter('instagram', transport=httpx.MockTransport(provider))
    content = PublishContent(body='Album', media=tuple(MediaInput(kind='image', url=f'https://assets.example/{index}.jpg') for index in range(2)))
    with pytest.raises(ChannelError, match='provider_http_error'): adapter.publish(account('instagram'), content)
    assert calls[-1] == ('DELETE', '/v25.0/child-1') and len(calls) == 3


def test_facebook_feed_video_uploads_requested_ranges_and_waits_for_processing(tmp_path):
    media = tmp_path / 'video.mp4'
    media.write_bytes(b'ABCDEFGH')
    processing = ['processing', 'ready']
    def provider(request):
        phase = request.url.params.get('upload_phase')
        if phase == 'start':
            assert request.url.params['file_size'] == '8'
            return httpx.Response(200, json={'upload_session_id': 'session', 'video_id': 'video-42', 'start_offset': '0', 'end_offset': '4'})
        if phase == 'transfer':
            if request.url.params['start_offset'] == '0':
                assert b'ABCD' in request.content and b'EFGH' not in request.content
                return httpx.Response(200, json={'start_offset': '4', 'end_offset': '8'})
            assert b'EFGH' in request.content and b'ABCD' not in request.content
            return httpx.Response(200, json={'start_offset': '8', 'end_offset': '8'})
        if request.method == 'POST':
            assert json.loads(request.content)['upload_session_id'] == 'session'
            return httpx.Response(200, json={'id': 'video-42'})
        if request.url.params.get('fields') == 'id,status':
            return httpx.Response(200, json={'id': 'video-42', 'status': {'video_status': processing.pop(0)}})
        return httpx.Response(200, json={'id': 'video-42', 'from': {'id': 'actor'}, 'permalink_url': 'https://www.facebook.com/actor/videos/video-42'})
    adapter = ChannelAdapter('facebook', transport=httpx.MockTransport(provider))
    staged = adapter.publish(account('facebook'), PublishContent(body='Video', media=(MediaInput(kind='video'),), options={'content_length': 8}))
    pending = adapter.upload(account('facebook'), staged, media)
    assert pending.state == 'pending' and pending.platform_work_id == 'video-42'
    assert adapter.finalize(account('facebook'), pending).state == 'pending'
    assert adapter.finalize(account('facebook'), pending).state == 'published'


@pytest.mark.parametrize('text,expected', [('汉' * 140, 280), ('é', 1), ('e\u0301', 1), ('👨‍👩‍👧‍👦', 2), ('🇨🇳', 2), ('https://example.com/' + 'a' * 300, 23)])
def test_twitter_v3_weighted_length(text, expected):
    assert weighted_length(text) == expected


def test_twitter_alt_text_is_written_before_create_and_overweight_text_stops():
    calls = []
    def provider(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/metadata'):
            assert json.loads(request.content) == {'id': 'media-1', 'metadata': {'alt_text': {'text': 'A photo'}}}
            return httpx.Response(200, json={'data': {'id': 'media-1'}})
        return httpx.Response(201, json={'data': {'id': 'work'}})
    adapter = ChannelAdapter('twitter', transport=httpx.MockTransport(provider))
    with pytest.raises(ChannelError, match='body_too_long'): adapter.publish(account('twitter'), PublishContent(body='汉' * 141))
    assert not calls
    adapter.publish(account('twitter'), PublishContent(body='Photo', media=(MediaInput(kind='image', media_id='media-1'),), options={'alt_text': 'A photo'}))
    assert calls == ['/2/media/metadata', '/2/tweets']


def test_tiktok_creator_duration_prevents_init_and_private_poll_preserves_identity():
    statuses = ['PUBLISHING', 'PUBLISH_COMPLETE']
    calls = []
    def provider(request):
        calls.append(request.url.path)
        if request.url.path.endswith('/creator_info/query/'):
            return httpx.Response(200, json={'data': {'privacy_level_options': ['SELF_ONLY'], 'max_video_post_duration_sec': 30}})
        return httpx.Response(200, json={'data': {'status': statuses.pop(0)}})
    adapter = ChannelAdapter('tiktok', transport=httpx.MockTransport(provider))
    with pytest.raises(ChannelError, match='creator_video_duration_exceeded'):
        adapter.publish(account('tiktok'), PublishContent(media=(MediaInput(kind='video', url='https://assets.example/video.mp4', metadata=MediaMetadata(duration_sec=31)),), options={'privacy_level': 'SELF_ONLY'}))
    assert len(calls) == 1
    receipt = PublishReceipt(state='pending', external_request_id='publish-1', details={'privacy_level': 'SELF_ONLY', 'media_kind': 'video'})
    pending = adapter.finalize(account('tiktok'), receipt)
    assert pending.state == 'pending' and pending.details['privacy_level'] == 'SELF_ONLY'
    complete = adapter.finalize(account('tiktok'), pending)
    assert complete.state == 'published' and not complete.platform_work_id and complete.external_request_id == 'publish-1'


def test_threads_topic_and_previous_cursor_are_native_provider_fields():
    def provider(request):
        if request.method == 'GET':
            assert request.url.params['before'] == 'previous' and request.url.params['limit'] == '12'
            return httpx.Response(200, json={'data': []})
        body = json.loads(request.content)
        assert body['text'] == 'Hello' and body['topic_tag'] == 'AI'
        return httpx.Response(200, json={'id': 'container'})
    adapter = ChannelAdapter('threads', transport=httpx.MockTransport(provider))
    adapter.works(account('threads'), 'previous', limit=12, direction='previous')
    adapter.publish(account('threads'), PublishContent(body='Hello #AI'))


def test_reply_parent_must_be_in_requested_work_before_any_write():
    calls = []
    def provider(request):
        calls.append(request.method)
        return httpx.Response(200, json={'data': [{'id': 'comment-owned'}]})
    adapter = ChannelAdapter('instagram', transport=httpx.MockTransport(provider))
    with pytest.raises(ChannelError, match='reply_parent_not_in_work'):
        adapter.comment(account('instagram'), 'work', 'Hello', reply_to_id='foreign-comment')
    assert calls == ['GET']


def test_tiktok_photo_completion_uses_actual_id_and_authenticated_creator_name():
    def provider(request):
        if request.url.path.endswith('/creator_info/query/'):
            return httpx.Response(200, json={'data': {'privacy_level_options': ['PUBLIC_TO_EVERYONE'], 'creator_username': 'own.creator'}})
        if request.url.path.endswith('/content/init/'):
            body = json.loads(request.content)
            assert body['post_info']['title'] == 'Photo title' and body['post_info']['description'] == 'Photo body'
            return httpx.Response(200, json={'data': {'publish_id': 'job'}})
        assert request.url.path.endswith('/status/fetch/')
        return httpx.Response(200, json={'data': {'status': 'PUBLISH_COMPLETE', 'publicly_available_post_id': ['12345']}})
    adapter = ChannelAdapter('tiktok', transport=httpx.MockTransport(provider))
    receipt = adapter.publish(account('tiktok'), PublishContent(title='Photo title', body='Photo body',
        media=(MediaInput(kind='image', url='https://assets.example/photo.jpg'),), options={'privacy_level': 'PUBLIC_TO_EVERYONE'}))
    completed = adapter.finalize(account('tiktok'), receipt)
    assert completed.platform_work_id == '12345' and completed.permalink == 'https://www.tiktok.com/@own.creator/photo/12345'


def test_youtube_owned_thumbnail_preserves_terminal_identity(tmp_path):
    path = tmp_path / 'cover.png'
    path.write_bytes(b'owned-cover-bytes')
    def provider(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'items': [{'id': 'video', 'snippet': {'channelId': 'actor'}}]})
        assert request.url.path == '/upload/youtube/v3/thumbnails/set' and request.url.params['videoId'] == 'video'
        assert request.content == b'owned-cover-bytes' and request.headers['content-type'] == 'image/png'
        return httpx.Response(200, json={'items': [{'default': {'url': 'https://i.ytimg.com/cover'}}]})
    adapter = ChannelAdapter('youtube', transport=httpx.MockTransport(provider))
    receipt = PublishReceipt(state='published', external_request_id='session', platform_work_id='video', permalink='https://www.youtube.com/watch?v=video')
    updated = adapter.apply_cover(account('youtube'), receipt, path)
    assert updated.platform_work_id == 'video' and updated.state == 'published' and updated.details['cover_status'] == 'applied'


def test_pinterest_short_link_redirects_are_checked_before_authenticated_read(monkeypatch):
    from urllib.request import Request
    from easel import remote_media
    destination = ['https://www.pinterest.com/pin/12345/']
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return destination[0]
    class Opener:
        def __init__(self, redirect): self.redirect = redirect
        def open(self, request, timeout):
            assert request.method == 'HEAD' and not request.get_header('Authorization')
            self.redirect.redirect_request(request, None, 302, '', {}, destination[0])
            return Response()
    monkeypatch.setattr(remote_media, 'public_opener', lambda redirect: Opener(redirect))
    calls = []
    def provider(request):
        calls.append(str(request.url))
        if request.url.path == '/v5/pins': return httpx.Response(200, json={'items': [{'id': '12345'}]})
        return httpx.Response(200, json={'id': '12345'})
    adapter = ChannelAdapter('pinterest', transport=httpx.MockTransport(provider))
    assert adapter.resolve_link(account('pinterest'), 'https://pin.it/short')['platform_work_id'] == '12345'
    destination[0] = 'https://127.0.0.1/private'
    before = len(calls)
    with pytest.raises(ChannelError, match='short_link_destination_invalid'): adapter.resolve_link(account('pinterest'), 'https://pin.it/bad')
    assert len(calls) == before
