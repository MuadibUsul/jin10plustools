from datetime import date,datetime,time,timezone
import hashlib
from sqlalchemy import select
import pytest

from app.briefs.policy import CATEGORIES,CHINA,first_run,previous_weekday,expected_date,production_window,next_slot
from app.briefs.service import ensure_issue,append_version,root_for
from app.briefs.worker import schedule,claim
from app.briefs.central_bank import parse_article
from app.db.models import BriefConfig,BriefJob,BriefVersion
from tests.conftest import login
from PIL import Image


def draft(db_factory,seed,warnings=None,valid=True):
    user=seed['editor']
    with db_factory() as db:
        issue=ensure_issue(db,user.team_id,date(2026,10,15),'liquidity')
        folder=root_for(user.team_id)/'runs/test';folder.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(800,600),'white').save(folder/'test.png')
        payload={'title':'核验标题','dynamic_body':'2026年10月15日，已核验数据。','data_date':'2026-10-15',
            'images':['test.png'],'image_sha256':{'test.png':hashlib.sha256((folder/'test.png').read_bytes()).hexdigest()},
            'artifact_dir':'runs/test','warnings':warnings or [],'facts':{},'valid':valid,'preview':True}
        append_version(db,issue,payload,user_id=user.id);db.commit();return str(issue.id)


def test_weekday_policy_has_no_holiday_calendar():
    assert previous_weekday(date(2026,10,19))==date(2026,10,16)
    assert previous_weekday(date(2026,10,20))==date(2026,10,19)
    assert production_window(datetime(2026,10,1,8,0,tzinfo=CHINA))
    assert not production_window(datetime(2026,10,17,8,0,tzinfo=CHINA))
    assert not production_window(datetime(2026,10,15,10,0,tzinfo=CHINA))
    assert expected_date('liquidity',date(2026,10,19))==date(2026,10,19)
    assert next_slot('liquidity',datetime(2026,10,15,9,55,tzinfo=CHINA)) is None


def test_portal_and_daily_routes_are_distinct(client):
    assert 'PLUS 工作台' in client.get('/').text
    assert 'id="sections"' in client.get('/daily/').text
    assert '固定快讯' in client.get('/briefs/').text
    assert 'src="/app.js"' in client.get('/daily/').text
    assert client.get('/api/health').json()=={'status':'ok'}
    assert client.get('/api/briefs/categories').status_code==401


def test_config_editor_viewer_roles(client):
    viewer=login(client,'viewer@plus.test');editor=login(client,'editor@plus.test');admin=login(client,'admin@plus.test')
    assert client.put('/api/briefs/config',json={'enabled':True},headers=viewer).status_code==403
    assert client.put('/api/briefs/config',json={'enabled':True},headers=editor).status_code==403
    assert client.put('/api/briefs/config',json={'enabled':True},headers=admin).status_code==200
    assert client.get('/api/briefs/categories',headers=admin).json()['schedule_enabled']


def test_draft_team_isolation_confirmation_and_viewer_files(client,db_factory,seed):
    id=draft(db_factory,seed,warnings=['来源口径待核对'])
    editor=login(client,'editor@plus.test');viewer=login(client,'viewer@plus.test');other=login(client,'out@plus.test')
    assert client.get(f'/api/briefs/issues/{id}',headers=other).status_code==404
    assert client.get(f'/api/briefs/issues/{id}',headers=viewer).json()['versions']==[]
    assert client.get(f'/api/briefs/issues/{id}/files/1/test.png',headers=viewer).status_code==403
    assert client.post(f'/api/briefs/issues/{id}/confirm',json={'version':1},headers=editor).status_code==200
    assert client.get(f'/api/briefs/issues/{id}/files/1/test.png',headers=viewer).status_code==200
    assert '注：短期货币市场' in client.get(f'/api/briefs/issues/{id}',headers=viewer).json()['versions'][0]['body']
    assert client.get(f'/api/briefs/issues/{id}/export',headers=viewer).status_code==404


def test_edit_conflict_keeps_old_confirmed_edition(client,db_factory,seed):
    id=draft(db_factory,seed);headers=login(client,'editor@plus.test')
    assert client.post(f'/api/briefs/issues/{id}/confirm',json={'version':1},headers=headers).status_code==200
    edit={'base_version':1,'title':'人工修改标题','dynamic_body':'人工修改正文。'}
    res=client.post(f'/api/briefs/issues/{id}/revisions',json=edit,headers=headers)
    assert res.status_code==201;assert res.json()['version']==2;assert res.json()['confirmed_version']==1
    assert client.post(f'/api/briefs/issues/{id}/revisions',json=edit,headers=headers).status_code==409
    assert client.post(f'/api/briefs/issues/{id}/confirm',json={'version':1},headers=headers).status_code==409
    assert '注：' in res.json()['versions'][0]['body']
    assert client.post(f'/api/briefs/issues/{id}/unconfirm',headers=headers).json()['confirmed_version'] is None


def test_corrupt_image_cannot_be_confirmed(client,db_factory,seed):
    id=draft(db_factory,seed);headers=login(client,'editor@plus.test')
    (root_for(seed['editor'].team_id)/'runs/test/test.png').write_bytes(b'changed')
    assert client.post(f'/api/briefs/issues/{id}/confirm',json={'version':1},headers=headers).status_code==409


def test_invalid_result_cannot_be_confirmed(client,db_factory,seed):
    id=draft(db_factory,seed,valid=False);headers=login(client,'editor@plus.test')
    assert client.post(f'/api/briefs/issues/{id}/confirm',json={'version':1},headers=headers).status_code==409


def test_duplicate_job_clicks_share_one_active_job(client):
    headers=login(client,'editor@plus.test');body={'category':'volume','issue_date':'2026-10-15'}
    a=client.post('/api/briefs/jobs',json=body,headers=headers);b=client.post('/api/briefs/jobs',json=body,headers=headers)
    assert a.status_code==202;assert a.json()['id']==b.json()['id']
    assert client.get('/api/briefs/issues?date=2026-10-15',headers=headers).json()['items']


def test_disabled_schedule_and_late_jobs(db_factory,seed,monkeypatch):
    now=datetime(2026,10,15,8,10,tzinfo=CHINA)
    # Queue timestamps come from this clock; freezing it keeps the later claim below
    # deterministic instead of depending on when the suite runs.
    monkeypatch.setattr('app.db.models.utcnow',lambda:now.astimezone(timezone.utc))
    assert schedule(db_factory,now)==0
    with db_factory() as db:
        db.add(BriefConfig(team_id=seed['editor'].team_id,enabled=True));db.commit()
    schedule(db_factory,now);schedule(db_factory,now)
    with db_factory() as db:assert len(db.scalars(select(BriefJob)).all())==sum(
        1 for category in CATEGORIES if first_run(category)<=time(8,10))
    # A later publication day exercises the expired-window branch.
    assert claim(db_factory,datetime(2026,10,16,10,0,tzinfo=CHINA)) is None
    with db_factory() as db:assert db.scalars(select(BriefJob).where(BriefJob.status=='skipped')).first().active_key is None


def test_pbc_table_extracts_exact_fields_and_checks_source_day():
    raw='<p>2026年10月15日全额满足一级交易商需求</p><table><tr><td>7天</td><td>1.40%</td><td>450亿元</td><td>450亿元</td></tr></table>'.encode()
    result=parse_article(raw,'2026-10-15')
    assert result['rate_percent']=='1.40';assert result['bid_yi']=='450';assert result['awarded_yi']=='450'
    with pytest.raises(ValueError):parse_article(raw,'2026-10-14')


def test_daily_material_cleanup_does_not_touch_brief_files(db_factory,seed):
    from app.services.maintenance import purge
    draft(db_factory,seed)
    with db_factory() as db:
        purge(db,7,datetime(2027,1,1,tzinfo=timezone.utc))
        assert db.scalars(select(BriefVersion)).first() is not None
    assert (root_for(seed['editor'].team_id)/'runs/test/test.png').exists()


def test_new_us_categories_accept_jobs(client):
    headers=login(client,'editor@plus.test')
    categories=[c['id'] for c in client.get('/api/briefs/categories',headers=headers).json()['categories']]
    assert categories[-2:]==['us_barometer','us_structure']
    for category in ('us_barometer','us_structure'):
        response=client.post('/api/briefs/jobs',headers=headers,
                             json={'category':category,'issue_date':'2026-10-16'})
        assert response.status_code==202, response.text
    assert client.post('/api/briefs/jobs',headers=headers,
                       json={'category':'unknown','issue_date':'2026-10-16'}).status_code==422
