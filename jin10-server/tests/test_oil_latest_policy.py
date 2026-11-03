import copy,json,uuid
from pathlib import Path
from datetime import date
from types import SimpleNamespace

from sqlalchemy import select
from app.briefs import sources
from app.briefs.body_templates import apply as apply_template
from app.briefs.data_reliability import extract,record_run
from app.briefs.runner import generate
from app.db.models import BriefJob,BriefDataCheck,BriefDataPoint


def official():
    path=Path(__file__).parents[1]/'app/briefs/samples/oil_etf/result.json'
    return copy.deepcopy(json.loads(path.read_text(encoding='utf-8'))['facts']['official_source'])


def test_latest_issuer_data_produces_with_no_historical_comparison(tmp_path,monkeypatch):
    sample=official();calls=[]
    def latest(out,expected,**kwargs):
        assert expected is None and kwargs['latest_per_fund'] is True
        calls.append(True);return sample
    monkeypatch.setattr('app.briefs.engines.oil_etf.run.fetch',latest)
    def forbidden(*args,**kwargs):raise AssertionError('No missing history should be fetched for production')
    monkeypatch.setattr('app.briefs.engines.oil_etf.previous.fetch',forbidden)
    out=tmp_path/'out';out.mkdir();state=tmp_path/'state';state.mkdir()
    result=sources.oil_etf('2026-11-02',out,state)
    assert calls and result['data_date']=='2026-10-14'
    assert result['requested_data_date']=='2026-11-02'
    assert result['data_date_policy']=='latest_available'
    assert all(f['change_barrels'] is None for f in result['facts']['funds'])
    assert not any(word in result['dynamic_body'] for word in ('增加','减少','持平'))
    assert '2026-10-14' in result['dynamic_body'] and '2026-11-02' not in result['dynamic_body']
    history=json.loads((state/'oil-history.json').read_text())
    assert set(history)=={'2026-10-14'}


def test_mixed_fund_dates_stay_separate_in_copy_history_and_database(tmp_path,monkeypatch):
    sample=official();sample['funds'][1]['data_date']='2026-10-13'
    sample['data_date']='2026-10-14'
    monkeypatch.setattr('app.briefs.engines.oil_etf.run.fetch',lambda *a,**kw:sample)
    out=tmp_path/'out';out.mkdir();state=tmp_path/'state';state.mkdir()
    payload=sources.oil_etf('2026-11-02',out,state)
    assert payload['fund_data_dates']=={'USO':'2026-10-14','BNO':'2026-10-13'}
    assert '2026-10-14，WTI' in payload['dynamic_body'] and '2026-10-13，Brent' in payload['dynamic_body']
    history=json.loads((state/'oil-history.json').read_text())
    assert set(history['2026-10-13'])=={'BNO'} and set(history['2026-10-14'])=={'USO'}
    assert {p['series']:p['date'] for p in extract('oil_etf',out,payload)}=={'USO':'2026-10-14','BNO':'2026-10-13'}
    original=payload['dynamic_body']
    apply_template(payload,'oil_etf',{'brief_templates':{'oil_etf':{'use_builtin':False,
        'template':'{date}，USO {uso_wan_barrels}万桶，BNO {bno_wan_barrels}万桶。'}}})
    assert payload['dynamic_body']==original and not payload.get('body_template_custom')


def test_latest_oil_passes_runner_date_gate_while_other_categories_remain_strict(tmp_path,monkeypatch):
    monkeypatch.setattr('app.briefs.engines.oil_etf.run.fetch',lambda *a,**kw:official())
    monkeypatch.setenv('SKILL_PRODUCTION','1')
    result=generate('oil_etf','2026-11-02',tmp_path,'latest-oil',action='refresh')
    assert result['valid'] and result['data_date']=='2026-10-14'
    assert result['requested_data_date']=='2026-11-02'
    import pytest
    monkeypatch.setitem(sources.ADAPTERS,'volume',lambda *a,**kw:{'data_date':'2026-10-14',
        'title':'test','dynamic_body':'test','facts':{},'images':['test.png']})
    with pytest.raises(ValueError,match='目标日期'):
        generate('volume','2026-11-02',tmp_path,'strict-volume',action='refresh')


def test_latest_oil_status_uses_actual_source_date_for_current_issue(client,db_factory,seed,tmp_path):
    from tests.conftest import login
    team=seed['editor'].team_id
    payload={'data_date':'2026-10-30','valid':True,'data_date_policy':'latest_available',
        'requested_data_date':'2026-11-02','fund_data_dates':{'USO':'2026-10-30','BNO':'2026-10-30'},
        'facts':{'funds':[{'ticker':ticker,'data_date':'2026-10-30','barrels':'1000000'} for ticker in ('USO','BNO')]}}
    with db_factory() as db:
        job=BriefJob(team_id=team,category='oil_etf',issue_date=date(2026,11,3),
            dedupe_key='latest-oil-policy',attempts=1,status='succeeded')
        db.add(job);db.flush();report=record_run(db,job,tmp_path,payload)
        assert report['current_data_valid'] and report['history_required_for_production'] is False
        assert any(m['missing_dates'] for m in report['metrics'].values())
        db.commit()
    response=client.get('/api/briefs/data-status?date=2026-11-03',headers=login(client,'viewer@plus.test'))
    assert response.status_code==200
    item=next(row for row in response.json()['items'] if row['category']=='oil_etf')
    assert item['target_date']=='2026-10-30' and item['report']['current_data_valid'] is True
