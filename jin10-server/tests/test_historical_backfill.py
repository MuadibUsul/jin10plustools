from datetime import date,timedelta
from pathlib import Path
import json

import pytest
from sqlalchemy import select
from app.briefs.repair import repair_ranges,restore_working_history
from app.briefs.published_history import oil_observations,import_oil_archive,METHOD
from app.briefs.data_reliability import save_points
from app.db.models import BriefJob,BriefDataPoint,BriefSourceSnapshot


def test_permanently_absent_early_gap_does_not_starve_later_ranges():
    wanted=['2026-01-01','2026-01-02','2026-02-02','2026-02-03','2026-03-02'];attempts={};calls=[]
    known=[{'date':'2026-01-02','close':'100'},{'date':'2026-02-03','close':'100'}]
    def fetch(first,last):
        calls.append(first)
        if len(calls)==1:return []
        return [{'date':'2026-02-02','close':'123'}]
    first,report=repair_ranges(known,wanted,fetch,max_requests=1,attempts=attempts)
    second,report=repair_ranges(first,wanted,fetch,max_requests=1,attempts=attempts)
    assert calls==[date(2025,12,29),date(2026,1,30)]
    assert next(row for row in second if row['date']=='2026-02-02')['close']=='123'


def test_source_failure_in_one_range_does_not_discard_other_real_records():
    wanted=['2026-01-01','2026-01-02','2026-02-02'];attempts={}
    def fetch(first,last):
        if first.year==2025:raise ValueError('source unavailable')
        return [{'date':'2026-02-02','close':'123'}]
    rows,report=repair_ranges([{'date':'2026-01-02','close':'100'}],wanted,fetch,attempts=attempts)
    assert next(row for row in rows if row['date']=='2026-02-02')['close']=='123'
    assert report['remaining_dates']==['2026-01-01'] and len(report['source_errors'])==1


def archive():
    return ('# Supplied published archive\n'+''.join(
        f'### 2026-10-{int(day[-2:])+1:02}\n- 发布时间：2026-10-{int(day[-2:])+1:02} 09:00:00\n'
        f'- 数据日：{day}\nWTI（USO）名义敞口约1883.4万桶；Brent（BNO）期货持仓约696.3万桶。\n'
        for day in ('2026-10-13','2026-10-14','2026-10-15','2026-10-16'))).encode()


def test_published_levels_keep_precision_without_inferred_changes():
    points=oil_observations(archive())
    assert len(points)==8
    uso=next(p for p in points if p['series']=='USO')
    assert uso['values']['barrels']=='18834000.0'
    assert uso['values']['reported_quantum_barrels']=='1000.0'
    assert uso['values']['approximate'] is True
    assert 'futures_barrels' not in uso['values'] and 'change_barrels' not in uso['values']


def test_published_import_requires_verified_overlap_and_never_changes_existing_data(db_factory,seed,tmp_path):
    team=seed['editor'].team_id
    with db_factory() as db:
        points=[{'date':day,'series':ticker,'method':'issuer_futures_and_swap_estimate_v1',
            'values':{'date':day,'ticker':ticker,'barrels':'18833983.62' if ticker=='USO' else '6963000'}}
            for day in ('2026-10-14','2026-10-15','2026-10-16') for ticker in ('USO','BNO')]
        save_points(db,team,'oil_etf',points)
        job=BriefJob(team_id=team,category='oil_etf',issue_date=date(2026,11,2),
            action='reconcile',dedupe_key='archive-import',attempts=1)
        db.add(job);db.flush()
        report=import_oil_archive(db,job,archive())
        assert report['restored_revisions']==2
        assert import_oil_archive(db,job,archive())['restored_revisions']==0
        imported=list(db.scalars(select(BriefDataPoint).where(BriefDataPoint.method==METHOD)))
        assert len(imported)==2 and all(row.source_snapshot_id for row in imported)
        assert imported[0].units['barrels']=='barrels'
        restore_working_history(db,team,'oil_etf',tmp_path)
        assert '2026-10-13' not in json.loads((tmp_path/'oil-history.json').read_text())
        other=BriefJob(team_id=seed['outsider'].team_id,category='oil_etf',issue_date=date(2026,11,2),
            action='reconcile',dedupe_key='other-import',attempts=1)
        db.add(other);db.flush()
        with pytest.raises(ValueError,match='重叠日期不足'):import_oil_archive(db,other,archive())
        bad=archive().replace(b'1883.4',b'9999.0')
        with pytest.raises(ValueError,match='超出披露精度'):import_oil_archive(db,job,bad)
        assert len(list(db.scalars(select(BriefSourceSnapshot))))==1


def test_gdp_published_later_requires_explicit_retrospective_mode(tmp_path):
    from app.briefs.valuation_repair import rebuild_buffett_history
    from app.briefs.sources import save,read
    path=tmp_path/'history.json';cap=tmp_path/'caps.json'
    original={'date':'2024-01-02','marketCap':'100','gdp':'200','close':'4000'}
    save(path,[original]);save(cap,[{'date':'2024-01-03','market_cap_yi':'110','source':'verified_fixture'}])
    gdp={'2023':{'value_yi':'210','published_on':'2024-01-17','source':'official_fixture'}}
    def closes(end,start,out,**kwargs):return {'source':'official_fixture','rows':[{'date':'2024-01-03','close':'4010'}]}
    before=rebuild_buffett_history(path,cap,'2024-01-03',gdp,tmp_path/'before',close_fetcher=closes)
    assert before['remaining_dates']==['2024-01-03'] and read(path)==[original]
    after=rebuild_buffett_history(path,cap,'2024-01-03',gdp,tmp_path/'after',close_fetcher=closes,allow_later_published_gdp=True)
    assert after['remaining_dates']==[] and read(path)[0]==original
    backcast=read(path)[1]
    assert backcast['gdp_available_on_observation_date'] is False
    assert backcast['source'].endswith('|retrospective_gdp_v1')


def test_valuation_working_history_is_restored_from_durable_observations(db_factory,seed,tmp_path):
    team=seed['editor'].team_id
    with db_factory() as db:
        save_points(db,team,'valuation',[{'date':'2024-01-03','series':'buffett','method':'source|retrospective_gdp_v1',
            'values':{'date':'2024-01-03','marketCap':'110','gdp':'210','close':'4010',
                'source':'source|retrospective_gdp_v1','gdp_available_on_observation_date':False}}])
        restore_working_history(db,team,'valuation',tmp_path)
        rows=json.loads((tmp_path/'buffett-history.json').read_text())
        assert rows[0]['date']=='2024-01-03' and rows[0]['gdp_available_on_observation_date'] is False
def test_background_gdp_catalog_keeps_current_override_and_historical_versions(tmp_path):
    from app.briefs.valuation_repair import gdp_catalog
    from app.briefs.sources import save
    save(tmp_path/'gdp-historical.json',{'2024':{'value_yi':'99','published_on':'2025-01-17'},'2025':{'value_yi':'1'}})
    save(tmp_path/'gdp.json',{'2025':{'value_yi':'100'}})
    assert gdp_catalog(tmp_path)=={'2024':{'value_yi':'99','published_on':'2025-01-17'},'2025':{'value_yi':'100'}}


def test_background_backfill_never_uses_gdp_before_publication_or_blocks_later_days(tmp_path):
    from app.briefs.valuation_repair import repair_buffett
    from app.briefs.sources import save,read
    history=tmp_path/'buffett-history.json'
    save(history,[{'date':'2026-10-27','marketCap':'99','gdp':'100','close':'4000','source':'original'}])
    calls=[]
    def cap(day,previous,out):
        calls.append(day)
        return {'source':'fixture','rows':[{'date':d,'market_cap_yi':'101'} for d in (previous,day)]}
    def close(day,previous,out,**kwargs):
        return {'rows':[{'date':d,'close':'4001'} for d in (previous,day)]}
    gdp={'2025':{'value_yi':'100','published_on':'2026-10-29'}}
    first=repair_buffett(history,'2026-10-29',tmp_path/'first',gdp,cap_fetcher=cap,close_fetcher=close)
    assert first['restored_dates']==['2026-10-29']
    assert first['remaining_dates']==['2026-10-28']
    assert first['unpublished_gdp_dates']==['2026-10-28']
    assert read(history)[-1]['gdp_available_on_observation_date'] is True
    assert {r['date'] for r in read(tmp_path/'all-a-history.json')}=={'2026-10-28','2026-10-29'}
    calls.clear()
    repair_buffett(history,'2026-10-29',tmp_path/'second',gdp,cap_fetcher=cap,close_fetcher=close)
    assert calls==['2026-10-29']


