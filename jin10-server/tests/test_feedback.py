import json
from types import SimpleNamespace

import pytest

from app.config import get_settings
from tests.conftest import login

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


def submit(client, headers, title='改进建议', content='希望导出支持按栏目筛选。', name='编辑甲', image=None):
    files = {'image': ('shot.png', image, 'image/png')} if image is not None else None
    return client.post('/api/feedback', headers=headers, data={'title': title, 'content': content, 'name': name},
                       files=files)


def test_viewer_submits_lists_and_reads_image(client, seed):
    headers = login(client, 'viewer@plus.test')
    assert client.get('/api/feedback', headers=headers).json()['items'] == []
    created = submit(client, headers, image=PNG)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body['title'] == '改进建议' and body['name'] == '编辑甲' and body['account'] == '只读'
    assert body['mine'] is True and body['has_image'] is True and body['image_mime'] == 'image/png'
    assert body['image_bytes'] == len(PNG)

    items = client.get('/api/feedback', headers=headers).json()['items']
    assert [item['title'] for item in items] == ['改进建议'] and items[0]['mine'] is True

    image = client.get(f"/api/feedback/{body['id']}/image", headers=headers)
    assert image.status_code == 200 and image.content == PNG
    assert image.headers['content-type'] == 'image/png'

    # Another team neither sees the item nor its image.
    other = login(client, 'out@plus.test')
    assert client.get('/api/feedback', headers=other).json()['items'] == []
    assert client.get(f"/api/feedback/{body['id']}/image", headers=other).status_code == 404


def test_list_is_newest_first_and_flags_owner(client, seed):
    headers = login(client, 'editor@plus.test')
    submit(client, headers, title='第一条')
    submit(client, headers, title='第二条')
    items = client.get('/api/feedback', headers=headers).json()['items']
    assert [item['title'] for item in items] == ['第二条', '第一条']
    # A different member sees the same team items but not as their own.
    viewer = login(client, 'viewer@plus.test')
    shared = client.get('/api/feedback', headers=viewer).json()['items']
    assert [item['mine'] for item in shared] == [False, False]


def test_validation_and_image_limits(client, seed, monkeypatch):
    headers = login(client, 'editor@plus.test')
    assert client.post('/api/feedback', headers=headers, data={'title': ' ', 'content': 'x', 'name': '甲'}).status_code == 422
    assert client.post('/api/feedback', headers=headers, data={'title': 'x', 'content': '', 'name': '甲'}).status_code == 422
    assert client.post('/api/feedback', headers=headers, data={'title': 'x', 'content': 'y', 'name': ' '}).status_code == 422
    assert submit(client, headers, image=b'<svg onload=alert(1)></svg>').status_code == 415
    settings = get_settings()
    monkeypatch.setattr(settings, 'max_feedback_bytes', 8)
    assert submit(client, headers, image=PNG).status_code == 413


def test_requires_login(client):
    assert client.get('/api/feedback').status_code == 401
    assert client.post('/api/feedback', data={'title': 'x', 'content': 'y', 'name': 'z'}).status_code == 401


def test_feedback_is_exportable_without_image_bytes(client, seed):
    headers = login(client, 'editor@plus.test')
    submit(client, headers, title='可导出的反馈', image=PNG)
    admin = login(client, 'admin@plus.test')
    payload = client.get('/api/export/feedback', headers=admin, params={'format': 'json'}).json()
    assert len(payload) == 1
    assert payload[0]['title'] == '可导出的反馈' and payload[0]['name'] == '编辑甲'
    assert payload[0]['user_id'] == '编辑甲' and payload[0]['has_image'] is True
    csv = client.get('/api/export/feedback', headers=admin, params={'format': 'csv'}).content
    assert b'\x89PNG' not in csv  # the image binary is never in the export


def test_feedback_mcp_completion_notifies_each_team_member(client, seed):
    admin = login(client, 'admin@plus.test')
    editor = login(client, 'editor@plus.test')
    viewer = login(client, 'viewer@plus.test')
    other = login(client, 'out@plus.test')
    token_path = '/api/feedback/agent-token'
    mcp_path = '/api/feedback/mcp'
    notices_path = '/api/feedback/notifications'
    assert client.post(token_path, headers=viewer).status_code == 403
    token = client.post(token_path, headers=admin).json()['token']
    agent = {'Authorization': f'Bearer {token}', 'Accept': 'application/json, text/event-stream'}

    def rpc(method, params=None, headers=agent):
        return client.post(mcp_path, headers=headers, json={
            'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {},
        })

    def call(name, **arguments):
        return rpc('tools/call', {'name': name, 'arguments': arguments}).json()['result']

    assert rpc('initialize', headers=admin).status_code == 401
    assert rpc('initialize', headers={**agent, 'Origin': 'https://outside.test'}).status_code == 403
    assert rpc('initialize', {'protocolVersion': '2025-06-18'}).json()['result']['protocolVersion'] == '2025-06-18'
    assert 'feedback_complete' in [tool['name'] for tool in rpc('tools/list').json()['result']['tools']]
    feedback_id = submit(client, editor, image=PNG).json()['id']
    outside_id = submit(client, other).json()['id']
    pending = json.loads(call('feedback_list_pending')['content'][0]['text'])['items']
    assert [row['id'] for row in pending] == [feedback_id]
    assert call('feedback_claim', feedback_id=outside_id)['isError']
    assert call('feedback_read_image', feedback_id=outside_id)['isError']
    assert call('feedback_read_image', feedback_id=feedback_id)['content'][0]['mimeType'] == 'image/png'

    claim = json.loads(call('feedback_claim', feedback_id=feedback_id)['content'][0]['text'])
    assert claim['status'] == 'processing'
    assert call('feedback_claim', feedback_id=feedback_id)['isError']
    assert client.get('/api/feedback', headers=viewer).json()['items'][0]['status'] == 'processing'
    assert not call('feedback_release', feedback_id=feedback_id, claim_id=claim['claim_id']).get('isError')
    assert client.get('/api/feedback', headers=viewer).json()['items'][0]['status'] == 'pending'
    previous_claim = claim['claim_id']
    claim = json.loads(call('feedback_claim', feedback_id=feedback_id)['content'][0]['text'])
    assert client.get(notices_path, headers=viewer).json()['items'] == []
    args = {'feedback_id': feedback_id, 'claim_id': claim['claim_id'], 'resolution': '已上线并验证筛选功能。'}
    assert call('feedback_complete', **{**args, 'claim_id': previous_claim})['isError']
    first = call('feedback_complete', **args)
    assert json.loads(first['content'][0]['text'])['status'] == 'completed'
    assert call('feedback_complete', **args) == first
    assert call('feedback_complete', **{**args, 'resolution': '修改处理结果'})['isError']

    for member in (viewer, editor, admin):
        notice = client.get(notices_path, headers=member).json()['items']
        assert len(notice) == 1 and notice[0]['resolution'] == args['resolution']
    assert client.get(notices_path, headers=other).json()['items'] == []
    read_path = f'{notices_path}/{feedback_id}/read'
    assert client.post(read_path, headers=other).status_code == 404
    assert client.post(read_path, headers=viewer).status_code == 204
    assert client.post(read_path, headers=viewer).status_code == 204
    assert client.get(notices_path, headers=viewer).json()['items'] == []
    assert len(client.get(notices_path, headers=editor).json()['items']) == 1
    assert client.get('/api/feedback', headers=viewer).json()['items'][0]['status'] == 'completed'
    assert client.delete(token_path, headers=admin).status_code == 204
    assert rpc('tools/list').status_code == 401
