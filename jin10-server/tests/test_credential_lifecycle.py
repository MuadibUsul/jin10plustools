from app.db.models import Team
from app.config import get_settings
from app.services.team_settings import cipher,migrate_tokens,provider


def test_legacy_token_migration_survives_later_jwt_rotation(db_factory,seed,monkeypatch):
    old=cipher(get_settings().jwt_secret).encrypt(b'legacy-fixture-key').decode()
    with db_factory() as db:
        team=db.get(Team,seed['admin'].team_id)
        team.settings={'ai':{'deepseek':{'token':old},'openai':{'token':'damaged-fixture','enabled':False}}}
        db.commit()
        assert migrate_tokens(db)=={'migrated':1,'unreadable':1}
        assert team.settings['ai']['deepseek']['token']!=old
    monkeypatch.setattr(get_settings(),'jwt_secret','new-jwt-fixture-only-0123456789')
    with db_factory() as db:
        team=db.get(Team,seed['admin'].team_id)
        assert provider(team,'deepseek')['key']=='legacy-fixture-key'
        assert provider(team,'openai')['key']==''
        assert 'configuration_error' not in provider(team,'openai')


def test_unreadable_provider_is_visible_without_breaking_settings(client,seed,db_factory):
    from tests.conftest import login
    with db_factory() as db:
        team=db.get(Team,seed['admin'].team_id)
        team.settings={'ai':{'jev':{'token':'broken-fixture'}}};db.commit()
    response=client.get('/api/settings',headers=login(client,'admin@plus.test'))
    assert response.status_code==200
    value=response.json()['ai']['jev']
    assert value['configuration_error']=='saved_key_unreadable' and 'key' not in value
