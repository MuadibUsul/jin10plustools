import base64
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.api import chat2api
from tests.conftest import login


class FakeResponse:
    def __init__(self, text='', data=None):
        self.text = text
        self._data = data or {}

    def json(self):
        return self._data


def make_jwt(exp):
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b'=').decode()
    payload = base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).rstrip(b'=').decode()
    return f'{header}.{payload}.signature'


def test_chat2api_settings_are_admin_only(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    assert client.get('/api/chat2api').status_code == 401
    assert client.get('/api/chat2api', headers=login(client, 'editor@plus.test')).status_code == 403
    response = client.post('/api/chat2api/tokens', json={'text': 'eyJx'},
                           headers=login(client, 'viewer@plus.test'))
    assert response.status_code == 403


def test_unconfigured_node_reports_itself(client, monkeypatch):
    monkeypatch.delenv('PLUS_CHAT2API_PREFIX', raising=False)
    monkeypatch.setenv('PLUS_CHAT2API_TOKENS', '/nonexistent/plus-token.txt')
    headers = login(client, 'admin@plus.test')
    data = client.get('/api/chat2api', headers=headers).json()
    assert data['configured'] is False and data['online'] is False and data['tokens_count'] is None
    assert data['expiry_available'] is False
    assert client.post('/api/chat2api/tokens', json={'text': 'eyJx'}, headers=headers).status_code == 503


def test_status_reads_available_token_count_and_expiry(client, monkeypatch, tmp_path):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    future = 4102444800  # 2100-01-01 UTC
    jwt = make_jwt(future)
    tokens = tmp_path / 'token.txt'
    tokens.write_text(jwt + '\nv1.Mrefresh-token-value\n', encoding='utf-8')
    monkeypatch.setenv('PLUS_CHAT2API_TOKENS', str(tokens))
    def fake(method, path, **kwargs):
        assert method == 'GET' and path == '/tokens'
        return FakeResponse(text='当前可用 Tokens 数量：<span class="text-blue-600">3</span>')
    monkeypatch.setattr(chat2api, 'request', fake)
    response = client.get('/api/chat2api', headers=login(client, 'admin@plus.test'))
    data = response.json()
    assert data['configured'] is True and data['online'] is True and data['tokens_count'] == 3
    assert data['expiry_available'] is True
    assert data['earliest_expiry'] == datetime.fromtimestamp(future, tz=timezone.utc).isoformat()
    assert data['expired_tokens'] == 0 and data['unknown_expiry'] == 1
    assert jwt not in response.text


def test_status_marks_expired_access_tokens(client, monkeypatch, tmp_path):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    jwt = make_jwt(1)
    tokens = tmp_path / 'token.txt'
    tokens.write_text(jwt + '\n', encoding='utf-8')
    monkeypatch.setenv('PLUS_CHAT2API_TOKENS', str(tokens))
    monkeypatch.setattr(chat2api, 'request', lambda *args, **kwargs: FakeResponse(text=''))
    data = client.get('/api/chat2api', headers=login(client, 'admin@plus.test')).json()
    assert data['expired_tokens'] == 1
    assert data['earliest_expiry'] is None


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


def test_chunked_session_cookies_are_rejected_with_a_hint(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    headers = login(client, 'admin@plus.test')
    chunked = ('__Secure-next-auth.session-token.0=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..abc; '
               '__Secure-next-auth.session-token.1=vO00m3UP3vEXAMPLEvalue')
    response = client.post('/api/chat2api/tokens', json={'text': chunked}, headers=headers)
    assert response.status_code == 422 and '会话 Cookie' in response.json()['detail']
    raw = client.post('/api/chat2api/tokens',
                      json={'text': 'eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..abc.def.ghi'},
                      headers=headers)
    assert raw.status_code == 422


def test_offline_node_never_reports_success(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    def fake(*args, **kwargs):
        raise HTTPException(502, 'chat2api 节点暂不可用')
    monkeypatch.setattr(chat2api, 'request', fake)
    headers = login(client, 'admin@plus.test')
    data = client.get('/api/chat2api', headers=headers).json()
    assert data['configured'] is True and data['online'] is False and data['tokens_count'] is None
    assert client.post('/api/chat2api/tokens', json={'text': 'x'}, headers=headers).status_code == 502


def test_expiry_prefers_future_tokens_and_counts_expired(client, monkeypatch, tmp_path):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    future = int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp())
    past = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
    tokens = tmp_path / 'token.txt'
    tokens.write_text(make_jwt(past) + '\n' + make_jwt(future) + '\n', encoding='utf-8')
    monkeypatch.setenv('PLUS_CHAT2API_TOKENS', str(tokens))
    monkeypatch.setattr(chat2api, 'request', lambda *args, **kwargs: FakeResponse(text=''))
    data = client.get('/api/chat2api', headers=login(client, 'admin@plus.test')).json()
    assert data['expired_tokens'] == 1
    assert data['earliest_expiry'] == datetime.fromtimestamp(future, tz=timezone.utc).isoformat()


def test_upload_accepts_a_full_session_json_over_twenty_kilobytes(client, monkeypatch):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    jwt = make_jwt(4102444800)
    session = json.dumps({'accessToken': jwt, 'user': {'name': 'tester'}, 'marketing': 'x' * 25000})
    assert len(session) > 20000
    forwarded = {}
    def fake(method, path, **kwargs):
        forwarded.update(kwargs)
        return FakeResponse(data={'status': 'success', 'tokens_count': 1})
    monkeypatch.setattr(chat2api, 'request', fake)
    response = client.post('/api/chat2api/tokens', json={'text': session},
                           headers=login(client, 'admin@plus.test'))
    assert response.status_code == 200
    assert response.json() == {'tokens_count': 1, 'extracted': 1}
    assert forwarded['data']['text'] == jwt


def test_upload_skips_tokens_already_in_the_pool(client, monkeypatch, tmp_path):
    monkeypatch.setenv('PLUS_CHAT2API_PREFIX', 'c2a_test')
    jwt = make_jwt(4102444800)
    tokens = tmp_path / 'token.txt'
    tokens.write_text(jwt + '\n', encoding='utf-8')
    monkeypatch.setenv('PLUS_CHAT2API_TOKENS', str(tokens))
    called = []
    monkeypatch.setattr(chat2api, 'request', lambda *args, **kwargs: called.append(1) or FakeResponse())
    response = client.post('/api/chat2api/tokens', json={'text': jwt},
                           headers=login(client, 'admin@plus.test'))
    assert response.json() == {'tokens_count': None, 'extracted': 0, 'duplicate': True}
    assert not called
