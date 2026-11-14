from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from easel.local_user_router import create_router


def test_persona_user_info_location_locale_type_and_ai_preferences(tmp_path):
    app = FastAPI()
    def write(request: Request):
        if request.headers.get('x-write') != 'yes':
            raise HTTPException(403)
    app.include_router(create_router(lambda: tmp_path, lambda persona: persona in {'alice', 'bob'}, write))
    with TestClient(app, headers={'x-write': 'yes'}) as client:
        value = client.put('/api/local-user', params={'persona': 'alice'}, json={
            'name': 'Alice', 'avatar': 'https://example.com/avatar.png', 'locale': 'en-US',
            'location': {'lat': 30.7, 'lng': 120.7, 'city': '嘉兴'}, 'user_type': 'business_owner'})
        assert value.status_code == 200
        assert value.json()['location']['city'] == '嘉兴'
        assert value.json()['user_type'] == 'business_owner'
        assert client.get('/api/local-user', params={'persona': 'bob'}).json()['name'] == 'bob'
        preferences = {kind: {'default_model': kind + '-model', 'options': {'temperature': 0.2}}
                       for kind in ('image', 'edit', 'video', 'agent')}
        assert client.put('/api/local-user/ai', params={'persona': 'alice'}, json=preferences).status_code == 200
        partial = client.put('/api/local-user/ai/video', params={'persona': 'alice'},
                             json={'default_model': 'video-v2', 'options': {'duration': 8}}).json()
        assert partial['ai']['video']['default_model'] == 'video-v2'
        assert partial['ai']['image']['default_model'] == 'image-model'
        assert client.get('/api/local-user', params={'persona': 'bob'}).json()['ai']['video'] is None
        assert client.put('/api/local-user', params={'persona': 'alice'}, json={'location': {'lat': 91, 'lng': 0}}).status_code == 422
        assert client.put('/api/local-user', params={'persona': 'alice'}, json={'avatar': '/etc/passwd'}).status_code == 403
        assert client.put('/api/local-user/ai/video', params={'persona': 'alice'}, json={'default_model': ''}).status_code == 422
        assert client.get('/api/local-user', params={'persona': 'missing'}).status_code == 400
        client.headers.pop('x-write')
        assert client.put('/api/local-user', params={'persona': 'alice'}, json={'name': 'hijack'}).status_code == 403
