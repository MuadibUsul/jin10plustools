from app.db.models import BriefConfig, Team
from app.services.team_settings import provider
from tests.conftest import login


def test_settings_are_team_scoped_versioned_and_do_not_expose_keys(client, seed, db_factory):
    admin = login(client, seed['admin'].email)
    editor = login(client, seed['editor'].email)
    outsider = login(client, seed['outsider'].email)
    with db_factory() as db:
        team = db.get(Team, seed['admin'].team_id)
        team.settings = {'scoring': {'impact': 0.7}, 'unrelated': True}
        db.commit()
    initial = client.get('/api/settings', headers=admin).json()
    key = 'test-private-api-key-123456789'
    result = client.put('/api/settings', headers=admin, json={
        'revision': initial['revision'], 'notes': {'valuation': '团队估值注释'},
        'schedule_enabled': True, 'ai': {'deepseek': {'key': key, 'model': 'deepseek-chat'}}})
    assert result.status_code == 200
    assert key not in result.text and 'token' not in result.text
    data = result.json()
    assert data['ai']['deepseek']['configured'] and data['ai']['deepseek']['source'] == 'team'
    assert data['note_revisions'][0]['after']['valuation'] == '团队估值注释'
    with db_factory() as db:
        team = db.get(Team, seed['admin'].team_id)
        assert key not in str(team.settings)
        assert provider(team, 'deepseek')['key'] == key
        assert team.settings['scoring'] == {'impact': 0.7} and team.settings['unrelated']
        assert db.get(BriefConfig, team.id).enabled
    other = client.get('/api/settings', headers=outsider).json()
    assert not other['note_revisions']
    assert next(c['notes'] for c in other['categories'] if c['id'] == 'valuation') != '团队估值注释'
    assert not other['schedule_enabled']
    # Editor may edit notes, but cannot change credentials or scheduling.
    assert client.put('/api/settings', headers=editor, json={'revision': 1, 'ai': {}}).status_code == 403
    assert client.put('/api/settings', headers=editor, json={'revision': 1, 'notes': {'valuation': '新版注释'}}).status_code == 200
    assert client.put('/api/settings', headers=admin, json={'revision': 1, 'notes': {}}).status_code == 409
    viewer = login(client, seed['viewer'].email)
    assert client.put('/api/settings', headers=viewer, json={'revision': 2, 'notes': {}}).status_code == 403
    assert key not in client.get('/api/settings', headers=viewer).text


def test_key_preserve_disable_reset_and_validation_redaction(client, seed, db_factory, monkeypatch):
    from app.config import get_settings
    monkeypatch.setenv('PLUS_BRIEFS_DEEPSEEK_API_KEY', 'server-fallback-key')
    get_settings.cache_clear()
    admin = login(client, seed['admin'].email)
    key = 'team-secret-key-012345678'
    def save(revision, value):
        return client.put('/api/settings', headers=admin, json={'revision': revision, 'ai': {'deepseek': value}})
    assert save(0, {'key': key}).status_code == 200
    assert save(1, {'model': 'deepseek-chat'}).status_code == 200
    with db_factory() as db:
        assert provider(db.get(Team, seed['admin'].team_id), 'deepseek')['key'] == key
    assert save(2, {'enabled': False}).status_code == 200
    with db_factory() as db:
        assert provider(db.get(Team, seed['admin'].team_id), 'deepseek')['key'] == ''
    assert save(3, {'use_server_key': True, 'enabled': True}).json()['ai']['deepseek']['source'] == 'server'
    with db_factory() as db:
        assert provider(db.get(Team, seed['admin'].team_id), 'deepseek')['key'] == 'server-fallback-key'
    bad = save(4, {'key': key, 'unsupported': key})
    assert bad.status_code == 422 and key not in bad.text


def test_saved_team_key_activates_daily_worker_without_restart(client, seed, db_factory, monkeypatch):
    from app.ai import worker
    from tests.test_api import material, push
    admin = login(client, seed['admin'].email)
    client.put('/api/settings', headers=admin, json={'revision': 0, 'ai': {'jev': {'key': 'team-jev-key-0123456'}}})
    push(client, admin, material())
    seen = []
    def analyze(factory, client, material_id, attempts):
        seen.append(material_id)
    monkeypatch.setattr(worker, 'analyze_one', analyze)
    assert worker.run_once(db_factory, None) == 1 and len(seen) == 1

def test_live_settings_are_scoped_validated_and_editable(client, seed):
    editor = login(client, seed['editor'].email)
    outsider = login(client, seed['outsider'].email)
    result = client.put('/api/settings', headers=editor, json={'revision': 0, 'live_url': 'https://youtu.be/abcdefghijk'})
    assert result.status_code == 200
    assert result.json()['live_url'] == 'https://youtu.be/abcdefghijk'
    assert client.get('/api/settings', headers=outsider).json()['live_url'] == ''
    for url in ('javascript:alert(1)', 'http://example.com/live', 'https://user:pass@example.com'):
        assert client.put('/api/settings', headers=editor, json={'revision': 1, 'live_url': url}).status_code == 422
    assert client.put('/api/settings', headers=editor, json={'revision': 1, 'live_url': ''}).json()['live_url'] == ''


def test_notes_accept_every_registered_category(client):
    admin=login(client,'admin@plus.test')
    categories=client.get('/api/settings',headers=admin).json()['categories']
    assert [c['id'] for c in categories][-2:]==['us_barometer','us_structure']
    response=client.put('/api/settings',headers=admin,
                        json={'revision':0,'notes':{c['id']:'注：'+c['label'] for c in categories}})
    assert response.status_code==200, response.text
