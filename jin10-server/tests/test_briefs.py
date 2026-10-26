from datetime import date,datetime,time,timezone
import hashlib
import json
from sqlalchemy import select
import pytest

from app.briefs.policy import AUTO_CATEGORIES, CATEGORIES,CHINA,first_run,previous_weekday,expected_date,production_window,next_slot
from app.briefs.service import ensure_issue,append_version,root_for,version_out,artifact_path
from app.briefs.ai_compute_charts import token_chart
from app.briefs.delivery import default_notes
from app.briefs.worker import schedule,claim
from app.briefs.central_bank import parse_article
from app.db.models import BriefConfig,BriefIssue,BriefJob,BriefVersion
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
    assert client.head('/briefs/').status_code==200
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


def test_editing_compute_notes_redraws_an_immutable_image_and_strips_body_notes(client,db_factory,seed):
    editor=seed['editor']
    rows=[{'date':'2026-10-07','token_price':'0.9931'},
          {'date':'2026-10-14','token_price':'0.9750'}]
    folder=root_for(editor.team_id)/'runs'/'compute-notes-test'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'ai-compute-source.json').write_text(json.dumps(rows),encoding='utf-8')
    image=token_chart(rows,folder)
    original_notes=default_notes('token_cost')
    with db_factory() as db:
        issue=ensure_issue(db,editor.team_id,date(2026,10,14),'token_cost')
        payload={'title':'原稿','dynamic_body':'当期事实。','data_date':'2026-10-14',
                 'images':[image.name],'image_sha256':{image.name:hashlib.sha256(image.read_bytes()).hexdigest()},
                 'artifact_dir':'runs/compute-notes-test','fixed_notes':original_notes,
                 'chart_notes':original_notes,'valid':True,'preview':True}
        append_version(db,issue,payload,user_id=editor.id)
        db.commit()
        issue_id=issue.id
    changed='1、更新后的固定注释。\n2、仅在图片显示。'
    headers=login(client,'editor@plus.test')
    result=client.post(f'/api/briefs/issues/{issue_id}/revisions',headers=headers,json={
        'base_version':1,'title':'更新标题',
        'dynamic_body':'当期事实。\n--------\n1、更新后的固定注释。\n2、仅在图片显示。',
        'fixed_notes':changed,
    })
    assert result.status_code==201,result.text
    assert result.json()['versions'][0]['body']=='当期事实。'
    blank=client.post(f'/api/briefs/issues/{issue_id}/revisions',headers=headers,json={
        'base_version':2,'title':'不应保存','dynamic_body':'当期事实。','fixed_notes':''})
    assert blank.status_code==422
    with db_factory() as db:
        versions=list(db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue_id)
                                 .order_by(BriefVersion.number)))
        assert len(versions)==2
        issue=db.get(BriefIssue,issue_id)
        before=artifact_path(issue,versions[0],image.name)
        after=artifact_path(issue,versions[1],image.name)
        assert before!=after and before.read_bytes()!=after.read_bytes()
        assert versions[1].payload['fixed_notes']==versions[1].payload['chart_notes']==changed
        assert versions[0].payload['fixed_notes']==original_notes
        assert (after.parent/'ai-compute-source.json').is_file()


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
        1 for category in AUTO_CATEGORIES if first_run(category)<=time(8,10))
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
    assert categories[-2:]==['token_cost','gpu_rental']
    for category in ('token_cost','gpu_rental'):
        response=client.post('/api/briefs/jobs',headers=headers,
                             json={'category':category,'issue_date':'2026-10-16'})
        assert response.status_code==202, response.text
    assert client.post('/api/briefs/jobs',headers=headers,
                       json={'category':'unknown','issue_date':'2026-10-16'}).status_code==422


def test_active_job_cannot_silently_swap_action(client):
    headers=login(client,'editor@plus.test'); body={'category':'volume','issue_date':'2026-10-15'}
    assert client.post('/api/briefs/jobs',json=body,headers=headers).status_code==202
    swapped=client.post('/api/briefs/jobs',json={**body,'action':'render'},headers=headers)
    assert swapped.status_code==409 and '正在执行' in swapped.json()['detail']


def test_viewer_sees_the_confirmed_older_version(client,db_factory,seed):
    id=draft(db_factory,seed); headers=login(client,'editor@plus.test'); viewer=login(client,'viewer@plus.test')
    assert client.post(f'/api/briefs/issues/{id}/confirm',json={'version':1},headers=headers).status_code==200
    edit={'base_version':1,'title':'新版标题','dynamic_body':'新版正文。'}
    assert client.post(f'/api/briefs/issues/{id}/revisions',json=edit,headers=headers).status_code==201
    visible=client.get(f'/api/briefs/issues/{id}',headers=viewer).json()
    assert [v['number'] for v in visible['versions']]==[1]
    assert visible['versions'][0]['title']=='核验标题'
    assert client.get(f'/api/briefs/issues/{id}/files/1/test.png',headers=viewer).status_code==200


def test_schedule_skips_a_category_that_already_has_a_version(db_factory,seed,monkeypatch):
    now=datetime(2026,10,15,8,10,tzinfo=CHINA)
    monkeypatch.setattr('app.db.models.utcnow',lambda:now.astimezone(timezone.utc))
    with db_factory() as db:
        db.add(BriefConfig(team_id=seed['editor'].team_id,enabled=True))
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,15),'volume')
        append_version(db,issue,{'title':'已有','dynamic_body':'已有正文','data_date':'2026-10-15','images':[],'valid':True})
        db.commit()
    schedule(db_factory,now)
    with db_factory() as db:
        assert not db.scalars(select(BriefJob).where(BriefJob.category=='volume',BriefJob.issue_date==date(2026,10,15))).all()


def test_confirmed_version_survives_pruning(db_factory,seed):
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,15),'volume')
        first=append_version(db,issue,{'title':'v1','dynamic_body':'v1','data_date':'2026-10-15','images':[],'valid':True})
        issue.confirmed_version=first.number;db.flush()
        for number in range(2,6):
            append_version(db,issue,{'title':f'v{number}','dynamic_body':f'v{number}','data_date':'2026-10-15','images':[],'valid':True})
        db.commit()
        versions=list(db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id).order_by(BriefVersion.number)))
        assert first.number in [v.number for v in versions]
        assert issue.confirmed_version==first.number


def test_ai_candidate_is_marked_stale_after_the_body_changes(db_factory,seed):
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,15),'volume')
        body='原正文。'
        payload={'title':'标题','dynamic_body':body,'data_date':'2026-10-15','images':[],'valid':True,
                 'deepseek':{'status':'ok','source_body_sha':hashlib.sha256(body.encode()).hexdigest()}}
        version=append_version(db,issue,payload)
        db.commit()
        assert version_out(issue,version)['deepseek']['stale'] is False
        edited=append_version(db,issue,payload,body='改过的正文。')
        db.commit()
        assert version_out(issue,edited)['deepseek']['stale'] is True


def test_liquidity_falls_back_when_the_pbc_table_is_missing(tmp_path,monkeypatch):
    from app.briefs import sources
    snapshot={'quotes':{'quote_date':'2026-10-15','updated_at':'2026-10-15 10:00'},'history':{}}
    monkeypatch.setattr('app.briefs.engines.liquidity.fetch_data.fetch_snapshot',lambda out,day:snapshot)
    monkeypatch.setattr(sources.central_bank,'fetch',lambda day,out:(_ for _ in ()).throw(ValueError('未找到唯一7天期逆回购操作表')))
    monkeypatch.setattr(sources,'render_liquidity',lambda snap,out:{'title':'标题','dynamic_body':'正文','data_date':'2026-10-15','images':[],'warnings':[],'facts':{}})
    payload=sources.liquidity('2026-10-15',tmp_path,tmp_path/'state')
    assert payload['warnings'] and '未找到唯一7天期逆回购操作表' in payload['warnings'][0]


def test_untracked_ai_candidate_is_marked_for_retry(db_factory,seed):
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,15),'volume')
        version=append_version(db,issue,{'title':'标题','dynamic_body':'正文','data_date':'2026-10-15',
            'images':[],'valid':True,'deepseek':{'status':'ok'}})
        db.commit()
        assert version_out(issue,version)['deepseek']['untracked'] is True
