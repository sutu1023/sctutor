import json

import httpx
import pytest
from conftest import FakeModels
from fastapi.testclient import TestClient

from sctutor.app import create_app
from sctutor.config import UserError
from sctutor.models import Models
from sctutor.retrieval import Retriever


def test_api_pages_and_local_boundary(settings, store):
    app = create_app(settings, store, FakeModels())
    with TestClient(app, base_url='http://127.0.0.1') as client:
        assert client.get('/').status_code == 200
        assert 'SC-Tutor' in client.get('/').text
        assert client.get('/static/app.js').status_code == 200
        assert client.get('/api/health').json()['configured']['llm']
        assert 'test-secret' not in client.get('/api/health').text
        assert client.post('/api/sessions', headers={'Origin':'https://evil.example'}).status_code == 403
        assert client.get('/api/sessions', headers={'Host':'evil.example'}).status_code == 400
        assert client.post('/api/auth/login', json={'account':'student'}).status_code == 200
        sid = client.post('/api/sessions').json()['id']
        assert client.get('/api/sessions/'+sid).json()['turns'] == []
        assert client.post('/api/sessions/'+sid+'/turns', json={'message':'', 'level':5}).status_code == 422
        assert client.post('/api/sessions/'+sid+'/turns', json={'message':' '}).status_code == 409


async def test_api_sse_and_preview(settings, store, corpus):
    _, model = corpus
    app = create_app(settings, store, model, Retriever(store, model))
    # ASGITransport avoids cross-event-loop TestClient for graph execution.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
        assert (await c.post('/api/auth/login', json={'account':'student'})).status_code == 200
        sid = (await c.post('/api/sessions')).json()['id']
        url = '/api/sessions/'+sid+'/turns'
        response = await c.post(url, json={'message':'Copilot'})
        assert response.status_code == 200
        assert 'event: status' in response.text and 'event: answer' in response.text
        assert response.text.index('event: answer') > response.text.index('"stage": "check"')
        turn = (await c.get('/api/sessions/'+sid)).json()['turns'][-1]
        pid = turn['citations'][0]['id']
        assert (await c.get(f"/api/turns/{turn['id']}/citations/{pid}")).status_code == 409
        for _ in range(2):
            r = await c.post(url, json={'action':'upgrade', 'parent_turn_id':turn['id']})
            assert r.status_code == 200
            turn = (await c.get('/api/sessions/'+sid)).json()['turns'][-1]
        assert (await c.get(f"/api/turns/{turn['id']}/citations/{pid}")).status_code == 200


def test_missing_settings_dont_leak(settings, store):
    settings.llm_api_key = ''
    with TestClient(create_app(settings, store, FakeModels()), base_url='http://127.0.0.1') as c:
        assert c.post('/api/auth/login', json={'account':'student'}).status_code == 200
        sid = c.post('/api/sessions').json()['id']
        response = c.post('/api/sessions/'+sid+'/turns', json={'message':'test'})
        assert response.status_code == 409
        assert 'LLM_API_KEY' in response.text
        assert 'test-secret' not in repr(settings)
        assert not store.history(sid)


async def test_http_adapter_payload_and_metrics(settings, store):
    seen = []
    headers_seen = []
    def respond(r):
        data = json.loads(r.content)
        seen.append((r.url.path, data))
        headers_seen.append(r.headers)
        if r.url.path.endswith('/embeddings'):
            return httpx.Response(200, json={'data':[{'index':i, 'embedding':[1,2]} for i in range(len(data['input']))]})
        return httpx.Response(200, json={'choices':[{'message':{'content':'{"ok":true}'}}],
                                       'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5,'unsafe':'secret'}})
    model = Models(settings, store, httpx.MockTransport(respond))
    assert await model.embed(['a','b']) == [[1,2],[1,2]]
    assert await model.chat('system', {'q':'question'}) == {'ok':True}
    assert seen[0][1]['model'] == 'Qwen/Qwen3-Embedding-8B'
    assert seen[1][1]['model'] == 'deepseek-v4.1-flash'
    assert seen[1][1]['thinking'] == {'type':'disabled'}
    assert seen[1][1]['response_format'] == {'type':'json_object'}
    assert headers_seen[1]['user-agent'] == 'SC-Tutor/0.1'
    assert headers_seen[1]['x-opencode-session'] == model.utility_session_id
    assert 'x-opencode-session' not in headers_seen[0]
    calls = store.query('SELECT * FROM calls')
    assert calls[0]['usage'] is None
    assert json.loads(calls[1]['usage'])['total_tokens'] == 5
    assert 'unsafe' not in calls[1]['usage']


async def test_http_auth_error_no_retry_no_secret(settings, store):
    count = 0
    def respond(r):
        nonlocal count
        count += 1
        return httpx.Response(401, json={'error':'test-secret private-provider-payload'})
    model = Models(settings, store, httpx.MockTransport(respond))
    with pytest.raises(UserError) as error:
        await model.embed(['test'])
    assert count == 1 and 'test-secret' not in str(error.value)
    assert 'private-provider' not in str(error.value)


async def test_http_retry_and_invalid_embedding(settings, store, monkeypatch):
    async def sleep(_):
        pass
    monkeypatch.setattr('sctutor.models.asyncio.sleep', sleep)
    count = 0
    def respond(r):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={'data':[{'index':0,'embedding':[0,0]}]})
    with pytest.raises(UserError, match='向量格式'):
        await Models(settings, store, httpx.MockTransport(respond)).embed(['a'])
    assert count == 2


async def test_invalid_chat_format_fails_closed(settings, store):
    response = {'choices':[{'message':{'content':'not JSON'}}]}
    model = Models(settings, store, httpx.MockTransport(lambda r:httpx.Response(200,json=response)))
    with pytest.raises(UserError, match='JSON'):
        await model.chat('sys', {})
