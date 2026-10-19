from datetime import datetime
import pytest
from app.briefs.policy import CHINA
from app.db.models import BriefJob
from app.services import live_catalog
from tests.conftest import login

@pytest.mark.parametrize('hour,allowed',[(7,False),(8,True),(22,True),(23,False)])
def test_live_hours_and_catalog(client,seed,monkeypatch,hour,allowed):
    monkeypatch.setattr(live_catalog,'now',lambda:datetime(2026,10,16,hour,0,tzinfo=CHINA))
    auth=login(client,seed['editor'].email)
    data=client.get('/api/live/catalog',headers=auth).json()
    assert len(data['categories'])==4 and len(data['channels'])==32
    assert data['allowed']==allowed
    assert all('url' not in c for c in data['channels'])
    r=client.get('/api/live/play/news-02',headers=auth)
    assert r.status_code==(200 if allowed else 409)
    if allowed:assert '.m3u8' in r.json()['url']

def test_generation_blocks_live_and_team_overrides_stay_scoped(client,seed,db_factory,monkeypatch):
    monkeypatch.setattr(live_catalog,'now',lambda:datetime(2026,10,16,9,0,tzinfo=CHINA))
    auth=login(client,seed['editor'].email);other=login(client,seed['outsider'].email)
    assert client.put('/api/settings',headers=auth,json={'revision':0,'live_urls':{'news-02':'https://player.example.com/live'}}).status_code==200
    assert client.get('/api/live/play/news-02',headers=auth).json()['url']=='https://player.example.com/live'
    assert client.get('/api/live/play/news-02',headers=other).json()['url']!='https://player.example.com/live'
    assert client.put('/api/settings',headers=auth,json={'revision':1,'live_urls':{'fake':'https://example.com'}}).status_code==422
    assert client.put('/api/settings',headers=auth,json={'revision':1,'live_urls':{'news-02':'javascript:alert(1)'}}).status_code==422
    with db_factory() as db:
        from app.briefs.service import enqueue
        enqueue(db,seed['editor'].team_id,datetime(2026,10,16).date(),'volume',user_id=seed['editor'].id,unique=True);db.commit()
    data=client.get('/api/live/state',headers=auth).json()
    assert data['busy'] and not data['allowed']
    assert client.get('/api/live/play/news-02',headers=auth).status_code==409
    assert client.get('/api/live/play/news-02',headers=other).status_code==200


def test_removed_youtube_entries_are_not_exposed_or_playable(client,seed,monkeypatch):
    monkeypatch.setattr(live_catalog,'now',lambda:datetime(2026,10,16,9,0,tzinfo=CHINA))
    auth=login(client,seed['editor'].email)
    data=client.get('/api/settings',headers=auth).json()['live_catalog']
    assert len(data['channels'])==32
    assert all(c['access']!='youtube' for c in data['channels'])
    assert client.get('/api/live/play/news-01',headers=auth).status_code==404
    assert client.get('/api/live/play/nature-01',headers=auth).status_code==404


def test_zoo_variant_is_resolved_only_on_play_and_custom_urls_skip_resolution(client,seed,monkeypatch):
    import httpx
    from app.db.models import Team
    from unittest.mock import patch
    from types import SimpleNamespace
    channel={'url':'https://nzp-wowza01.si.edu/live_edge/foo/playlist.m3u8','select_variant':'first'}
    transport=httpx.MockTransport(lambda request:httpx.Response(200,text='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=6000000\nhigh.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=350000\ndead.m3u8\n'))
    original=httpx.Client
    with patch('httpx.Client',side_effect=lambda **kwargs:original(transport=transport,**kwargs)):
        assert live_catalog.player_url(channel).endswith('/high.m3u8')
    with pytest.raises(ValueError):live_catalog.player_url({**channel,'url':'https://private.example/playlist.m3u8'})
    data=live_catalog.catalog(SimpleNamespace(settings={'live_urls':{'nature-11':'https://example.com/custom.m3u8'}}))
    custom=next(c for c in data['channels'] if c['id']=='nature-11')
    assert 'select_variant' not in custom
    assert live_catalog.player_url(custom)=='https://example.com/custom.m3u8'
