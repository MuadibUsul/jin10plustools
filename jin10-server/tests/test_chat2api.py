from fastapi import HTTPException

from app.api import chat2api
from tests.conftest import login


class FakeResponse:
    def __init__(self, text='', data=None):
        self.text = text
        self._data = data or {}

    def json(self):
        return self._data


def test_chat2api_settings_are_admin_only(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    assert client.get('/api/chat2api').status_code == 401
    assert client.get('/api/chat2api', headers=login(client, 'editor@plus.test')).status_code == 403
    response = client.post('/api/chat2api/tokens', json={'text': 'eyJx'},
                           headers=login(client, 'viewer@plus.test'))
    assert response.status_code == 403


def test_unconfigured_node_reports_itself(client, monkeypatch):
    monkeypatch.delenv('PLUS_CHAT2API_PREFIX', raising=False)
    headers = login(client, 'admin@plus.test')
    assert client.get('/api/chat2api', headers=headers).json() == {
        'configured': False, 'online': False, 'tokens_count': None}
    assert client.post('/api/chat2api/tokens', json={'text': 'eyJx'}, headers=headers).status_code == 503


def test_status_reads_available_token_count(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    def fake(method, path, **kwargs):
        assert method == 'GET' and path == '/tokens'
        return FakeResponse(text='当前可用 Tokens 数量：<span class="text-blue-600">3</span>')
    monkeypatch.setattr(chat2api, 'request', fake)
    assert client.get('/api/chat2api', headers=login(client, 'admin@plus.test')).json() == {
        'configured': True, 'online': True, 'tokens_count': 3}


def test_upload_clear_and_error_tokens(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    calls = []
    def fake(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == '/tokens/upload':
            return FakeResponse(data={'status': 'success', 'tokens_count': 2})
        if path == '/tokens/clear':
            return FakeResponse(data={'status': 'success', 'tokens_count': 0})
        if path == '/tokens/error':
            return FakeResponse(data={'status': 'success', 'error_tokens': ['bad']})
        raise AssertionError(path)
    monkeypatch.setattr(chat2api, 'request', fake)
    headers = login(client, 'admin@plus.test')
    assert client.post('/api/chat2api/tokens', json={'text': 'eyJx\nv1.M'},
                       headers=headers).json() == {'tokens_count': 2, 'extracted': 2}
    assert calls[0][2] == {'data': {'text': 'eyJx\nv1.M'}}
    assert client.post('/api/chat2api/clear', headers=headers).json() == {'tokens_count': 0}
    assert client.get('/api/chat2api/errors', headers=headers).json() == {'error_tokens': ['bad']}


def test_upload_extracts_tokens_from_a_whole_session_json(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    forwarded = {}
    def fake(method, path, **kwargs):
        forwarded.update(kwargs)
        return FakeResponse(data={'status': 'success', 'tokens_count': 2})
    monkeypatch.setattr(chat2api, 'request', fake)
    jwt = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature_part-123'
    refresh = 'v1.Mabc_DEF-1234567890.more'
    session = ('{"user":{"id":"u1","name":"tester"},"accessToken":"' + jwt
               + '","refreshToken":"' + refresh + '","sessionToken":"eyJhbGciOiJkaXIifQ..abc.def.ghi","expires":"2099-01-01"}')
    response = client.post('/api/chat2api/tokens', json={'text': session},
                           headers=login(client, 'admin@plus.test'))
    assert response.json() == {'tokens_count': 2, 'extracted': 2}
    assert forwarded['data']['text'].splitlines() == [jwt, refresh]


def test_upload_reads_cookie_lines_and_rejects_text_without_tokens(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    forwarded = {}
    def fake(method, path, **kwargs):
        forwarded.update(kwargs)
        return FakeResponse(data={'status': 'success', 'tokens_count': 1})
    monkeypatch.setattr(chat2api, 'request', fake)
    jwt = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature_part-123'
    cookie = '__Secure-next-auth.session-token=' + jwt + '; other=1'
    headers = login(client, 'admin@plus.test')
    assert client.post('/api/chat2api/tokens', json={'text': cookie},
                       headers=headers).json() == {'tokens_count': 1, 'extracted': 1}
    assert forwarded['data']['text'] == jwt
    rejected = client.post('/api/chat2api/tokens', json={'text': '{"user":{"id":"u1"}}'},
                           headers=headers)
    assert rejected.status_code == 422


def test_offline_node_never_reports_success(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    def fake(*args, **kwargs):
        raise HTTPException(502, 'chat2api 节点暂不可用')
    monkeypatch.setattr(chat2api, 'request', fake)
    headers = login(client, 'admin@plus.test')
    assert client.get('/api/chat2api', headers=headers).json() == {
        'configured': True, 'online': False, 'tokens_count': None}
    assert client.post('/api/chat2api/tokens', json={'text': 'x'}, headers=headers).status_code == 502
