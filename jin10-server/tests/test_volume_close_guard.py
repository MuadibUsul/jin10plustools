"""A pre-close turnover print is not a verified close and cannot be confirmed."""
from datetime import date
from decimal import Decimal
import hashlib
import json

import pytest
from sqlalchemy import select

from app.briefs.data_reliability import save_points
from app.briefs.service import append_version, ensure_issue, root_for
from app.briefs.trading_calendar import previous_session
from app.db.models import BriefDataPoint, BriefIssue, BriefVersion
from tests.conftest import login


def _page(rows):
    class Reply:
        def __enter__(self):return self
        def __exit__(self, *args):return None
        def read(self, *args):return json.dumps({'data':rows}).encode()
    return Reply()


def test_pre_close_print_is_not_saved_as_the_daily_close(tmp_path, monkeypatch):
    from app.briefs import sources
    from app.briefs.series_store import merge_history
    state=tmp_path/'state';state.mkdir();out=tmp_path/'run';out.mkdir()
    noon={'date':'2026-11-23','turn':'9163.71158132','vol':'671.614390'}
    merge_history(state/'volume-history.json',[noon],method='jin10_hushen_turnover_yi_v1')
    raw=[{'date':'2026-11-23','sh_turn':'4000','sz_turn':'5163.71158132','sh_vol':'300',
          'sz_vol':'371.614390','updated_at':'2026-11-23T11:59:00+08:00'}]
    monkeypatch.setattr(sources,'urlopen',lambda *args,**kwargs:_page(raw))
    monkeypatch.setattr(sources,'completed_session',lambda *_:date(2026,11,23))
    with pytest.raises(ValueError,match='早于当日15:00'):
        sources.volume('2026-11-23',out,state)
    assert (out/'volume-raw-page-1.json').is_file()
    history=json.loads((state/'volume-history.json').read_text(encoding='utf-8'))
    assert all(row['turn']!=noon['turn'] for row in history['rows'])
    assert json.loads((out/'volume-intraday-rejected.json').read_text(encoding='utf-8'))[0]['date']=='2026-11-23'


def test_post_close_update_still_publishes_the_same_source_total(tmp_path, monkeypatch):
    from app.briefs import charts,sources
    end=date(2026,11,23)
    days=[end]
    while len(days)<21:
        days.append(previous_session('xshg',days[-1]))
    rows=[{'date':day.isoformat(),'sh_turn':'7000','sz_turn':'6000','sh_vol':'100','sz_vol':'200'}
          for day in reversed(days)]
    rows[-1]['updated_at']='2026-11-23T15:01:00+08:00'
    monkeypatch.setattr(sources,'urlopen',lambda *args,**kwargs:_page(rows))
    monkeypatch.setattr(sources,'completed_session',lambda *_:end)
    monkeypatch.setattr(charts,'volume_chart',lambda *_:tmp_path/'unused.png')
    state=tmp_path/'state';state.mkdir();out=tmp_path/'run';out.mkdir()
    result=sources.volume('2026-11-23',out,state)
    assert result['facts']['amount_yi']=='13000'
    assert Decimal(result['facts']['amount_yi'])!=Decimal('9163.71158132')


def test_confirm_blocks_a_retained_pre_close_draft_and_keeps_its_values(client,db_factory,seed):
    from PIL import Image
    team=seed['editor'].team_id
    raw={'data':[{'date':'2026-11-23','sh_turn':'4000','sz_turn':'5163.71158132','sh_vol':'300',
        'sz_vol':'371.614390','updated_at':'2026-11-23T11:59:00+08:00'}]}
    intraday={'date':'2026-11-23','turn':'9163.71158132','vol':'671.614390'}
    with db_factory() as db:
        save_points(db,team,'volume',[{'series':'volume','method':'jin10_hushen_turnover_yi_v1',
            'date':intraday['date'],'values':dict(intraday)}])
        issue=ensure_issue(db,team,date(2026,11,24),'volume')
        folder=root_for(team)/'runs'/'noon-draft'
        folder.mkdir(parents=True)
        (folder/'plot-data.json').write_text(json.dumps([intraday]),encoding='utf-8')
        (folder/'volume-raw-page-1.json').write_text(json.dumps(raw),encoding='utf-8')
        Image.new('RGB',(40,20),'white').save(folder/'chart.png')
        payload={'title':'原稿','dynamic_body':'盘中原稿保留。','data_date':'2026-11-23','images':['chart.png'],
            'image_sha256':{'chart.png':hashlib.sha256((folder/'chart.png').read_bytes()).hexdigest()},
            'artifact_dir':'runs/noon-draft','facts':{'source':'https://www.jin10.com/example'},
            'valid':True,'preview':True}
        append_version(db,issue,payload,user_id=seed['editor'].id)
        db.commit()
        issue_id=issue.id
        point_id=db.scalar(select(BriefDataPoint.id).where(BriefDataPoint.team_id==team))
        original=json.loads((folder/'plot-data.json').read_text(encoding='utf-8'))
    headers=login(client,'editor@plus.test')
    blocked=client.post(f'/api/briefs/issues/{issue_id}/confirm',json={'version':1},headers=headers)
    assert blocked.status_code==409
    assert '重新生成' in blocked.json()['detail']
    with db_factory() as db:
        point=db.get(BriefDataPoint,point_id)
        assert point.values['turn']==intraday['turn'] and point.values['vol']==intraday['vol']
        assert point.quality_status=='rejected' and '15:00' in point.quality_reason
        assert db.get(BriefIssue,issue_id).confirmed_version is None
        version=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==issue_id,BriefVersion.number==1))
        assert version.payload['dynamic_body']=='盘中原稿保留。'
        assert json.loads((root_for(team)/'runs'/'noon-draft'/'plot-data.json').read_text(encoding='utf-8'))==original
        fresh=root_for(team)/'runs'/'close-draft'
        fresh.mkdir()
        Image.new('RGB',(40,20),'white').save(fresh/'chart.png')
        (fresh/'plot-data.json').write_text(json.dumps([
            {'date':'2026-11-23','turn':'15528.8017283684','vol':'1119.564804',
             'updated_at':'2026-11-23T15:05:00+08:00'}]),encoding='utf-8')
        (fresh/'volume-raw-page-1.json').write_text(json.dumps({'data':[{
            'date':'2026-11-23','sh_turn':'8000','sz_turn':'7528.8017283684','sh_vol':'500','sz_vol':'619.564804',
            'updated_at':'2026-11-23T15:05:00+08:00'}]}),encoding='utf-8')
        append_version(db,db.get(BriefIssue,issue_id),{
            'title':'收盘稿','dynamic_body':'已核验收盘。','data_date':'2026-11-23','images':['chart.png'],
            'image_sha256':{'chart.png':hashlib.sha256((fresh/'chart.png').read_bytes()).hexdigest()},
            'artifact_dir':'runs/close-draft','facts':{'source':'https://www.jin10.com/example','amount_yi':'15528.8017283684'},
            'valid':True,'preview':True},user_id=seed['editor'].id)
        db.commit()
    adopted=client.post(f'/api/briefs/issues/{issue_id}/confirm',json={'version':2},headers=headers)
    assert adopted.status_code==200,adopted.text
    assert adopted.json()['confirmed_version']==2
    with db_factory() as db:
        assert db.get(BriefDataPoint,point_id).values['turn']==intraday['turn']


def test_flash_backfill_draft_cannot_be_confirmed(client,db_factory,seed):
    from PIL import Image
    team=seed['editor'].team_id
    with db_factory() as db:
        issue=ensure_issue(db,team,date(2026,11,24),'volume')
        folder=root_for(team)/'runs'/'flash-draft'
        folder.mkdir(parents=True)
        Image.new('RGB',(40,20),'white').save(folder/'chart.png')
        append_version(db,issue,{
            'title':'快讯稿','dynamic_body':'快讯原稿保留。','data_date':'2026-11-20','images':['chart.png'],
            'image_sha256':{'chart.png':hashlib.sha256((folder/'chart.png').read_bytes()).hexdigest()},
            'artifact_dir':'runs/flash-draft',
            'facts':{'source':'https://flash.jin10.com/detail/20261120150127681800'},
            'valid':True,'preview':True},user_id=seed['editor'].id)
        db.commit();issue_id=issue.id
    blocked=client.post(f'/api/briefs/issues/{issue_id}/confirm',json={'version':1},
                        headers=login(client,'editor@plus.test'))
    assert blocked.status_code==409 and '快讯' in blocked.json()['detail']
    with db_factory() as db:
        version=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==issue_id))
        assert version.payload['dynamic_body']=='快讯原稿保留。'
        assert db.get(BriefIssue,issue_id).confirmed_version is None
