from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import pytest
from sqlalchemy import select

from app.briefs import trading_calendar as cal
from app.briefs.policy import CHINA, expected_date, production_window
from app.briefs.repair import repair_ranges,restore_working_history,recover_snapshots
from app.briefs.series_store import merge_history
from app.briefs.data_reliability import save_points,coverage
from app.briefs.sources import normalize_volume
from app.briefs.public_compute_store import store_visible_history,restore_workbook_history
from app.db.models import BriefDataPoint,BriefDataCheck,BriefConfig,BriefJob,AIComputeObservation,AIComputeRevision


@pytest.mark.parametrize('category,production,expected',[
    ('volume','2026-10-08','2026-09-30'),('liquidity','2026-10-11','2026-10-10'),
    ('volume','2026-10-11','2026-10-09'),('us_structure','2026-07-06','2026-07-02'),
    ('oil_etf','2026-04-06','2026-04-02'),('gpu_rental','2026-10-19','2026-10-18'),
    ('us_barometer','2025-01-10','2025-01-08'),('us_structure','2022-01-01','2021-12-31')])
def test_verified_market_dates(category,production,expected):
    assert expected_date(category,date.fromisoformat(production)).isoformat()==expected


def test_calendar_does_not_invent_an_unpublished_year():
    with pytest.raises(cal.CalendarUnavailable):cal.is_session('xshg','2027-02-10')
    assert production_window(datetime(2026,10,31,8,30,tzinfo=CHINA))
    assert cal.is_session('cfets','2026-10-10') and not cal.is_session('xshg','2026-10-10')


def volume_rows():
    days=cal.sessions('xshg','2026-08-01','2026-10-30')
    return [{'date':day,'sh_turn':'7000','sz_turn':'6000','sh_vol':'100','sz_vol':'200'} for day in days]


def test_volume_really_requires_missing_session_and_accepts_repaired_data():
    rows=volume_rows();missing=[row for row in rows if row['date']!='2026-10-29']
    with pytest.raises(ValueError,match='2026-10-29'):normalize_volume({'data':missing},'2026-10-30')
    fixed=normalize_volume({'data':rows},'2026-10-30')
    assert fixed[-2]['date']=='2026-10-29'
    assert fixed[-1]['ma20']==13000


def test_range_fetch_actually_fills_holes_and_checkpoints_progress():
    points=[{'date':'2026-10-27','close':'100'},{'date':'2026-10-29','close':'102'}]
    calls=[];saved=[]
    def fetch(first,last):
        calls.append((first,last));return [{'date':'2026-10-28','close':'101'}]
    result,report=repair_ranges(points,['2026-10-27','2026-10-28','2026-10-29'],fetch,checkpoint=saved.append)
    assert len(calls)==1 and len(result)==3 and report['remaining_dates']==[] and saved[-1]==result
    # A resumed run reads saved observations and performs zero redundant fetches.
    resumed,report=repair_ranges(saved[-1],[row['date'] for row in result],fetch)
    assert report['requests']==0 and resumed==result


def test_range_source_conflict_is_not_silently_scaled_or_overwritten():
    with pytest.raises(ValueError,match='不一致'):
        repair_ranges([{'date':'2026-10-27','close':'100'}],['2026-10-27','2026-10-28'],
            lambda *args:[{'date':'2026-10-27','close':'200'},{'date':'2026-10-28','close':'201'}])


def test_shorter_history_never_erases_prefix_and_methods_do_not_mix(tmp_path):
    path=tmp_path/'history.json'
    merge_history(path,[{'date':'2026-10-27','close':'100'},{'date':'2026-10-28','close':'101'}],method='source_a')
    result=merge_history(path,[{'date':'2026-10-28','close':'102'}],method='source_a')
    assert [row['date'] for row in result]==['2026-10-27','2026-10-28']
    assert result[-1]['close']=='102'
    with pytest.raises(ValueError,match='禁止'):merge_history(path,[],method='source_b')


def point(day,value='100'):
    return {'series':'volume','date':day,'method':'jin10_hushen_turnover_yi_v1',
            'values':{'date':day,'turn':value,'vol':'100'}}


def test_append_only_revisions_and_database_restoration(db_factory,seed,tmp_path):
    team=seed['editor'].team_id
    with db_factory() as db:
        assert save_points(db,team,'volume',[point('2026-10-28'),point('2026-10-29')])==2
        assert save_points(db,team,'volume',[point('2026-10-29','101')])==1
        assert save_points(db,team,'volume',[point('2026-10-29','101')])==0
        restore_working_history(db,team,'volume',tmp_path)
        stored=json.loads((tmp_path/'volume-history.json').read_text())
        assert len(stored['rows'])==2 and stored['rows'][-1]['turn']=='101'
        history=list(db.scalars(select(BriefDataPoint).where(BriefDataPoint.data_date==date(2026,10,29)).order_by(BriefDataPoint.revision)))
        assert [row.values['turn'] for row in history]==['100','101']
        assert coverage(db,seed['outsider'].team_id,'volume','2026-10-29')['metrics']['volume']['observed_days']==0


def test_workbook_restoration_only_inserts_real_absent_past_dates(db_factory,seed):
    with db_factory() as db:
        team=seed['editor'].team_id
        first=restore_workbook_history(db,team,before='2026-10-15')
        assert first>1000
        assert restore_workbook_history(db,team,before='2026-10-15')==0
        assert db.scalar(select(AIComputeObservation).where(AIComputeObservation.team_id==team,
            AIComputeObservation.date==date(2026,10,15))) is None
        # The uploaded workbook itself has this gap: do not invent its value.
        assert db.scalar(select(AIComputeObservation).where(AIComputeObservation.team_id==team,
            AIComputeObservation.kind=='token',AIComputeObservation.date==date(2026,9,11))) is None


def test_visible_history_fills_real_days_and_preserves_a_revision(db_factory,seed):
    with db_factory() as db:
        team=seed['editor'].team_id
        capture={'category':'token_cost','data_date':'2026-10-29',
            'history':{'token':[{'date':'2026-10-28','price':'0.9750'}]}}
        assert store_visible_history(db,team,capture)['inserted']==1
        capture['history']['token'][0]['price']='0.9740'
        assert store_visible_history(db,team,capture)['revised']==1
        old=db.scalar(select(AIComputeRevision))
        assert old.values['token_price']=='0.975000'


def test_reconciliation_rechecks_success_and_retries_on_later_cycles(db_factory,seed):
    from app.briefs.worker import schedule_reconciliation,claim
    now=datetime(2026,10,30,14,tzinfo=CHINA);team=seed['editor'].team_id
    with db_factory() as db:db.add(BriefConfig(team_id=team,enabled=True));db.commit()
    assert schedule_reconciliation(db_factory,now)==1
    assert schedule_reconciliation(db_factory,now)==0
    with db_factory() as db:
        job=db.scalar(select(BriefJob));assert job.action=='reconcile'
        job.next_attempt_at=now.astimezone(timezone.utc);db.commit()
    assert claim(db_factory,now)==job.id
    with db_factory() as db:
        row=db.get(BriefJob,job.id);row.status='failed';row.active_key=None;db.commit()
    assert schedule_reconciliation(db_factory,now.replace(hour=20))==1


def test_data_status_is_team_scoped_and_available_to_viewers(client,db_factory,seed):
    from tests.conftest import login
    with db_factory() as db:
        db.add(BriefDataCheck(team_id=seed['outsider'].team_id,category='volume',
            data_date=date(2026,10,29),status='failed',report={'private':'not-visible'}));db.commit()
    response=client.get('/api/briefs/data-status?date=2026-10-30',headers=login(client,'viewer@plus.test'))
    assert response.status_code==200
    assert 'not-visible' not in response.text and len(response.json()['items'])==9


def test_erp_backfill_restores_all_inputs_and_resumes_without_repeating(tmp_path):
    from app.briefs.valuation_repair import repair_erp
    from app.briefs.sources import save,read
    path=tmp_path/'erp.json';save(path,[{'date':'2026-10-27','close':'4000',
        'pe_ttm_mcw':'15','bond_yield_number':'2'}])
    calls=[]
    def pe(out):calls.append('pe');return {'2026-10-28':'15.12','2026-10-29':'15.34'}
    def closes(end,start,out,**kwargs):
        assert kwargs['all_between']
        return {'rows':[{'date':'2026-10-28','close':'4100'},{'date':'2026-10-29','close':'4200'}]}
    def bonds(*args):return {'normalized':[{'date':day,'yield_source_text':'2.0123'} for day in ('2026-10-28','2026-10-29')]}
    report=repair_erp(path,'2026-10-29',tmp_path/'source',pe_fetcher=pe,close_fetcher=closes,bond_fetcher=bonds)
    assert report['restored_dates']==['2026-10-28','2026-10-29'] and report['remaining_dates']==[]
    assert len(read(path))==3 and read(path)[-1]['pe_ttm_mcw_raw']=='15.34'
    assert repair_erp(path,'2026-10-29',tmp_path/'resume',pe_fetcher=pe)['restored_dates']==[]
    assert calls==['pe']


def test_erp_source_absence_is_not_disguised_as_success(tmp_path):
    from app.briefs.valuation_repair import repair_erp
    from app.briefs.sources import save,read
    path=tmp_path/'erp.json';save(path,[{'date':'2026-10-27'}])
    report=repair_erp(path,'2026-10-29',tmp_path/'source',pe_fetcher=lambda _: {})
    assert report['remaining_dates']==['2026-10-28','2026-10-29'] and len(read(path))==1


def test_volume_pages_and_retains_older_history_instead_of_overwriting(tmp_path,monkeypatch):
    from app.briefs import sources,charts
    from urllib.parse import urlparse,parse_qs
    state=tmp_path/'state';state.mkdir();out=tmp_path/'run';out.mkdir()
    merge_history(state/'volume-history.json',[{'date':'2025-01-02','turn':'12000','vol':'200'}],
        method='jin10_hushen_turnover_yi_v1')
    days=cal.sessions('xshg','2025-09-01','2026-10-30')
    all_rows=[{'date':day,'sh_turn':'7000','sz_turn':'6000','sh_vol':'100','sz_vol':'200'} for day in reversed(days)]
    calls=[]
    class Reply:
        def __init__(self,raw):self.raw=raw
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return self.raw
    def fetch(request,**kwargs):
        page=int(parse_qs(urlparse(request.full_url).query)['page'][0]);calls.append(page)
        return Reply(json.dumps({'data':all_rows[(page-1)*100:page*100]}).encode())
    monkeypatch.setattr(sources,'urlopen',fetch)
    monkeypatch.setattr(charts,'volume_chart',lambda rows,out:out/'chart.png')
    result=sources.volume('2026-10-30',out,state)
    saved=json.loads((state/'volume-history.json').read_text())
    assert len(calls)>=3 and saved['rows'][0]['date']=='2025-01-02'
    assert result['facts']['previous_date']=='2026-10-29'


def test_liquidity_restores_an_expired_quote_from_real_daily_history(tmp_path,monkeypatch):
    from app.briefs.engines.liquidity import fetch_data
    quotes=json.dumps({'head':{'rep_code':'200'},'data':{'showDateCN':'2026-10-30 10:00'},
                       'records':[]}).encode()
    csv=b'2026-10-30,,,,,,1.2,1.3,1.4\n2026-10-29,,,,,,1.25,1.35,1.45\n2026-10-28,,,,,,1.26,1.36,1.46\n'
    monkeypatch.setattr(fetch_data,'read_source',lambda name,*args,**kwargs:(quotes if name.endswith('json') else csv,{'url':name}))
    snapshot=fetch_data.fetch_snapshot(tmp_path,'2026-10-29')
    assert snapshot['quotes']['provenance']=='official_published_daily_history'
    assert snapshot['quotes']['rows'][0]['weighted_rate']=='1.25'
    assert snapshot['quotes']['rows'][0]['latest_rate'] is None
    assert snapshot['history']['end']=='2026-10-29'
    assert snapshot['quotes']['source_actual_updated_at']=='2026-10-30 10:00'


def test_successful_repair_never_overwrites_an_editorial_draft(db_factory,seed,monkeypatch):
    from app.briefs.worker import execute
    from app.briefs.service import enqueue,root_for
    from app.db.models import BriefVersion
    team=seed['editor'].team_id
    with db_factory() as db:
        job=enqueue(db,team,date(2026,10,30),'volume',action='reconcile',cycle='test')
        job.status='running';job.attempts=1;db.commit();job_id=job.id
    folder=root_for(team)/'runs'/str(job_id);folder.mkdir(parents=True)
    (folder/'result.json').write_text(json.dumps({'valid':True,'title':'Source facts',
        'dynamic_body':'Source facts','data_date':'2026-10-29','artifact_dir':'runs/'+str(job_id),
        'images':[],'facts':{}}))
    class Process:
        def __init__(self,*args,**kwargs):pass
        def poll(self):return 0
        def wait(self):return 0
    monkeypatch.setattr('app.briefs.worker.subprocess.Popen',Process)
    monkeypatch.setattr('app.briefs.skill_production.apply_skill_copy',lambda *args:pytest.fail('Data-only repair called AI'))
    execute(db_factory,job_id)
    with db_factory() as db:
        assert db.get(BriefJob,job_id).status=='succeeded'
        assert db.scalar(select(BriefVersion)) is None
        assert db.scalar(select(BriefDataCheck)) is not None


def test_background_ai_collection_stores_data_without_generating_content(db_factory,seed,monkeypatch):
    from app.briefs.worker import execute
    from app.briefs.service import enqueue
    from app.db.models import BriefVersion
    team=seed['editor'].team_id
    with db_factory() as db:
        job=enqueue(db,team,date(2026,10,30),'token_cost',action='reconcile',cycle='test')
        job.status='running';job.attempts=1;db.commit();job_id=job.id
    def capture(factory,id,out):
        with factory() as db:
            db.add(AIComputeObservation(team_id=team,kind='token',date=date(2026,10,29),
                token_price='0.96',revision=1,source='Verified visible card',source_status='public_visible_summary'))
            db.commit()
        return {'data_date':'2026-10-29'}
    monkeypatch.setattr('app.briefs.worker.capture_public_for_job',capture)
    monkeypatch.setattr('app.briefs.worker.subprocess.Popen',lambda *args,**kwargs:pytest.fail('Collector generated content'))
    execute(db_factory,job_id)
    with db_factory() as db:
        assert db.get(BriefJob,job_id).status=='succeeded'
        assert db.scalar(select(BriefVersion)) is None
        assert db.scalar(select(BriefDataPoint)).values['price']=='0.960000'


def test_both_ai_sources_get_data_repair_jobs_but_not_morning_generation(db_factory,seed):
    from app.briefs.worker import schedule_reconciliation
    from app.briefs.policy import AUTO_CATEGORIES,CATEGORIES
    assert not {'token_cost','gpu_rental'} & set(AUTO_CATEGORIES)
    team=seed['editor'].team_id;now=datetime(2026,10,30,14,tzinfo=CHINA)
    with db_factory() as db:db.add(BriefConfig(team_id=team,enabled=True));db.commit()
    categories=set()
    for _ in range(9):
        assert schedule_reconciliation(db_factory,now)==1
        with db_factory() as db:
            job=db.scalar(select(BriefJob).where(BriefJob.status=='queued'))
            categories.add(job.category);job.status='succeeded';job.active_key=None;db.commit()
    assert categories==set(CATEGORIES)


def test_as_of_replay_retains_original_values_after_correction_and_exports_units(db_factory,seed):
    from app.briefs.data_reliability import read_series
    from app.services.data_export import DATASETS
    team=seed['editor'].team_id
    with db_factory() as db:
        save_points(db,team,'volume',[point('2026-10-28','100')],collected_at=datetime(2026,10,29,0,tzinfo=timezone.utc))
        save_points(db,team,'volume',[point('2026-10-28','101')],collected_at=datetime(2026,10,30,0,tzinfo=timezone.utc))
        early=read_series(db,team,'volume','volume',date(2026,10,28),date(2026,10,28),
            as_of=datetime(2026,10,29,12,tzinfo=timezone.utc))
        current=read_series(db,team,'volume','volume',date(2026,10,28),date(2026,10,28))
        assert early['items'][0]['values']['turn']=='100'
        assert current['items'][0]['values']['turn']=='101'
        assert current['items'][0]['units']['turn']=='CNY_100_million'
        assert len(list(DATASETS['brief_data_points'].rows(db,team,None,None)))==2


def test_late_import_of_older_evidence_does_not_replace_the_current_value(db_factory,seed,tmp_path):
    from app.briefs.data_reliability import read_series
    team=seed['editor'].team_id
    with db_factory() as db:
        save_points(db,team,'volume',[point('2026-10-28','102')],collected_at=datetime(2026,10,30,tzinfo=timezone.utc))
        save_points(db,team,'volume',[point('2026-10-28','100')],collected_at=datetime(2026,10,29,tzinfo=timezone.utc))
        assert read_series(db,team,'volume','volume',date(2026,10,28),date(2026,10,28))['items'][0]['values']['turn']=='102'
        restore_working_history(db,team,'volume',tmp_path)
        assert json.loads((tmp_path/'volume-history.json').read_text())['rows'][0]['turn']=='102'
        assert save_points(db,team,'volume',[point('2026-10-28','103')],collected_at=datetime(2026,10,31,tzinfo=timezone.utc))==1


def test_daily_storage_can_remain_enabled_without_automatic_drafts(client,db_factory,seed):
    from tests.conftest import login
    from app.briefs.worker import schedule,schedule_reconciliation
    admin=login(client,'admin@plus.test');current=client.get('/api/settings',headers=admin).json()
    response=client.put('/api/settings',headers=admin,json={'revision':current['revision'],
        'schedule_enabled':False,'data_collection_enabled':True})
    assert response.status_code==200 and response.json()['data_collection_enabled']
    assert schedule(db_factory,datetime(2026,10,30,8,30,tzinfo=CHINA))==0
    assert schedule_reconciliation(db_factory,datetime(2026,10,30,14,tzinfo=CHINA))==1
    assert client.put('/api/settings',headers=login(client,'editor@plus.test'),
        json={'revision':response.json()['revision'],'data_collection_enabled':False}).status_code==403


def test_ma9_does_not_silently_shift_across_a_known_missing_session(tmp_path):
    from app.briefs.engines.turnover.build_turnover import load_and_calculate
    days=cal.sessions('xshg','2026-08-01','2026-10-30')
    records=[{'date':day,'totalCompany':5000,'belowPercent3':3000,'belowPercent5':4500,'close':'4000'}
             for day in days if day!='2026-10-15']
    path=tmp_path/'raw.json';path.write_text(json.dumps(records))
    calculated=load_and_calculate(path,session_dates=days)
    available={row['date'] for row in calculated}
    assert '2026-10-16' not in available and '2026-10-27' not in available
    assert '2026-10-28' in available and '2026-10-30' in available


def test_evidence_path_changes_do_not_create_fake_numeric_revisions(db_factory,seed):
    with db_factory() as db:
        row=point('2026-10-28');row['values'].update(source_file='/old/first.json',source_sha256='old')
        assert save_points(db,seed['editor'].team_id,'volume',[row])==1
        row['values'].update(source_file='/new/second.json',source_sha256='new')
        assert save_points(db,seed['editor'].team_id,'volume',[row])==0


def test_partial_gpu_histories_keep_the_available_values(db_factory,seed):
    with db_factory() as db:
        capture={'category':'gpu_rental','data_date':'2026-10-29',
            'history':{'a100':[{'date':'2026-10-28','price':'1.56'}],
                       'h100':[{'date':'2026-10-28','price':'2.89'}]},'readings':[]}
        assert store_visible_history(db,seed['editor'].team_id,capture)['inserted']==1
        row=db.scalar(select(AIComputeObservation))
        assert str(row.a100)=='1.560000' and row.b200 is None


def test_all_a_historical_universe_does_not_require_an_unopened_exchange():
    from app.briefs.engines.valuation.fetch_free_all_a import normalize_all_a,FetchAllAError
    def raw(days,include_bj=False):
        rows=[]
        for day in days:
            for code,exchange in [('600000','SH'),('000001','SZ')]+([('830001','BJ')] if include_bj else []):
                rows.append({'TRADE_DATE':day+' 00:00:00','SECURITY_CODE':code,
                    'SECUCODE':code+'.'+exchange,'TOTAL_MARKET_CAP':1000,'CLOSE_PRICE':10,'TOTAL_SHARES':100})
        return [json.dumps({'success':True,'code':0,'result':{'pages':1,'count':len(rows),'data':rows}}).encode()]
    historical=normalize_all_a(raw(['2019-04-30','2019-05-06']),'2019-05-06','2019-04-30')
    assert historical['rows'][0]['exchange_counts']=={'SH':1,'SZ':1,'BJ':0}
    with pytest.raises(FetchAllAError):
        normalize_all_a(raw(['2026-10-28','2026-10-29']),'2026-10-29','2026-10-28')
    with pytest.raises(FetchAllAError):
        normalize_all_a(raw(['2019-04-30','2019-05-06'],True),'2019-05-06','2019-04-30')


def test_market_cap_is_kept_when_index_source_fails_and_cached_input_can_resume(tmp_path):
    from app.briefs.valuation_repair import repair_buffett
    from app.briefs.sources import save,read
    history=tmp_path/'buffett-history.json'
    save(history,[{'date':'2026-10-27','marketCap':'100','gdp':'1000','close':'4000','source':'original'}])
    calls=[]
    def cap(day,previous,out):
        calls.append(day)
        return {'source':'verified_fixture','rows':[{'date':stamp,'market_cap_yi':'101'} for stamp in (previous,day)]}
    def unavailable(*args,**kwargs):raise RuntimeError('index unavailable')
    gdp={'2025':{'value_yi':'1000'}}
    first=repair_buffett(history,'2026-10-29',tmp_path/'first',gdp,cap_fetcher=cap,close_fetcher=unavailable)
    assert {row['date'] for row in read(tmp_path/'all-a-history.json')}=={'2026-10-28','2026-10-29'}
    assert first['remaining_raw_dates']==[] and first['remaining_dates']==['2026-10-28','2026-10-29']
    def close(day,previous,out,**kwargs):
        return {'rows':[{'date':day,'close':'4001'}]}
    second=repair_buffett(history,'2026-10-29',tmp_path/'second',gdp,cap_fetcher=cap,close_fetcher=close)
    assert calls==['2026-10-29','2026-10-29']
    assert second['remaining_dates']==[]
    assert read(history)[0]['source']=='original'


def test_market_cap_backfill_advances_without_an_unknown_gdp_denominator(tmp_path):
    from app.briefs.valuation_repair import repair_buffett
    from app.briefs.sources import save,read
    history=tmp_path/'buffett-history.json'
    save(history,[{'date':'2026-10-26','marketCap':'100','gdp':'1000','close':'4000','source':'original'}])
    calls=[]
    def cap(day,previous,out):
        calls.append(day)
        return {'source':'verified_fixture','rows':[{'date':stamp,'market_cap_yi':'101'} for stamp in (previous,day)]}
    def close(day,previous,out,**kwargs):
        return {'rows':[{'date':stamp,'close':'4001'} for stamp in (previous,day)]}
    first=repair_buffett(history,'2026-10-29',tmp_path/'first',{},cap_fetcher=cap,close_fetcher=close)
    assert first['remaining_raw_dates']==[] and first['remaining_dates']==['2026-10-27','2026-10-28','2026-10-29']
    assert set(first['unavailable_gdp_years'])=={'2025'}
    calls.clear()
    repair_buffett(history,'2026-10-29',tmp_path/'second',{},cap_fetcher=cap,close_fetcher=close)
    assert calls==['2026-10-29']
    assert len(read(history))==1


def test_data_migration_preserves_team_switches_and_builds_postgres_ddl(tmp_path,monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.config import get_settings
    import io
    import sqlite3
    import uuid
    path=tmp_path/'migration.sqlite'
    monkeypatch.setenv('PLUS_DATABASE_URL','sqlite:///'+path.as_posix())
    get_settings.cache_clear()
    try:
        command.upgrade(Config('alembic.ini'),'0017')
        teams=[uuid.uuid4().hex,uuid.uuid4().hex]
        with sqlite3.connect(path) as db:
            for team,enabled in zip(teams,[0,1]):
                db.execute("INSERT INTO teams(id,name,settings,created_at) VALUES (?, 'fixture', '{}', '2026-10-30')",(team,))
                db.execute("INSERT INTO brief_configs(team_id,enabled,notes,updated_at) VALUES (?, ?, '{}', '2026-10-30')",(team,enabled))
        command.upgrade(Config('alembic.ini'),'0018')
        with sqlite3.connect(path) as db:
            for team,expected in zip(teams,[0,1]):
                assert db.execute('SELECT enabled,data_enabled FROM brief_configs WHERE team_id=?',(team,)).fetchone()==(expected,expected)
            columns={row[1] for row in db.execute('PRAGMA table_info(brief_data_points)')}
            assert {'values','units','source_snapshot_id','collected_at','revision'}<=columns
        monkeypatch.setenv('PLUS_DATABASE_URL','postgresql+psycopg://migration_fixture@localhost/unused')
        get_settings.cache_clear()
        config=Config('alembic.ini');config.output_buffer=io.StringIO()
        command.upgrade(config,'0017:0018',sql=True)
        ddl=config.output_buffer.getvalue()
        assert 'JSONB' in ddl and 'CREATE TABLE brief_data_points' in ddl and 'CREATE TABLE brief_data_checks' in ddl
    finally:
        get_settings.cache_clear()
