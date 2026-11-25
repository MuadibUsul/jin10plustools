from datetime import date
from decimal import Decimal
import csv
import io

from app.briefs.data_reliability import save_points
from app.briefs.buffett_observations import backfill_us_buffett
from app.services import data_export
from tests.conftest import login


def test_a_share_buffett_export_is_flat_latest_dated_and_team_scoped(client,db_factory,seed):
    team=seed['editor'].team_id
    def point(amount,day='2026-11-23'):
        return {'series':'buffett','date':day,'method':'eastmoney_public_all_a_company_total_capitalization',
            'values':{'date':day,'marketCap':amount,'gdp':'1401879','source':'eastmoney_public',
                'gdp_vintage':{'period':'2025全年','source':'https://www.stats.gov.cn/'}}}
    with db_factory() as db:
        save_points(db,team,'valuation',[point('1200000')])
        save_points(db,team,'valuation',[point('1200100')])
        save_points(db,team,'valuation',[point('1200200','2026-11-24')])
        save_points(db,seed['outsider'].team_id,'valuation',[point('999')]);db.commit()
    headers=login(client,'viewer@plus.test')
    params={'start':'2026-11-23','end':'2026-11-23','format':'json'}
    response=client.get('/api/export/a_share_buffett',headers=headers,params=params)
    assert response.status_code==200
    rows=response.json();assert len(rows)==1
    row=rows[0]
    assert row['market_cap']=='1200100' and row['revision']==2
    assert Decimal(str(row['buffett_pct']))==Decimal(f"{Decimal('1200100')/Decimal('1401879')*100:.2f}")
    assert row['gdp_period']=='2025全年' and row['gdp_source']=='https://www.stats.gov.cn/'
    count=client.get('/api/export/a_share_buffett/count',headers=headers,params=params).json()
    assert count['count']==1
    params['format']='csv'
    csv_rows=list(csv.DictReader(io.StringIO(client.get('/api/export/a_share_buffett',headers=headers,params=params).content.decode('utf-8-sig'))))
    assert len(csv_rows)==1 and csv_rows[0]['市值分子']=='1200100'
    assert '巴菲特指数（%）' in csv_rows[0]


def test_us_buffett_backfill_exports_inputs_and_current_revision_limit(db_factory,seed):
    team=seed['editor'].team_id
    with db_factory() as db:
        save_points(db,team,'us_barometer',[
            {'series':'wilshire','date':'2026-11-23','method':'yahoo_w5000_v1',
             'values':{'date':'2026-11-23','close':'72118.2'}},
            {'series':'gdp','date':'2026-07-01','method':'fred_current_revised_not_point_in_time',
             'values':{'period':'2026-07-01','value':'33000'}}])
        assert backfill_us_buffett(db,team)==1
        assert backfill_us_buffett(db,team)==0
        rows=list(data_export.DATASETS['us_buffett'].rows(db,team,date(2026,11,23),date(2026,11,23)))
        assert len(rows)==1
        row=rows[0]
        assert row['buffett_pct']=='218.54' and row['market_cap']=='72118.2' and row['gdp']=='33000'
        assert row['gdp_period']=='2026-07-01'
        assert row['gdp_vintage_policy']=='current_revised_not_point_in_time'
        assert row['market_cap_unit']=='Wilshire指数点，非美元市值' and '季度年化' in row['gdp_unit']
        assert row['quality_status']=='unverified'
        assert not list(data_export.DATASETS['us_buffett'].rows(db,seed['outsider'].team_id,None,None))


def test_incomplete_historical_buffett_inputs_remain_empty(db_factory,seed):
    team=seed['editor'].team_id
    with db_factory() as db:
        save_points(db,team,'valuation',[{'series':'buffett','date':'2020-01-02',
            'method':'legacy_buffett_method_unverified','values':{'date':'2020-01-02','marketCap':'100'}}])
        row=list(data_export.DATASETS['a_share_buffett'].rows(db,team,None,None))[0]
        assert row['buffett_pct'] is None and row['gdp'] is None
        assert row['source_status']=='inputs_incomplete'


def test_new_us_capture_adds_exportable_proxy_without_changing_snapshot(tmp_path,db_factory,seed):
    import json
    from app.briefs.data_reliability import extract
    from app.db.models import BriefDataPoint
    from sqlalchemy import select
    snapshot={'wilshire':[{'date':'2026-11-23','close':'72118.2'}],
        'gdp':[{'period':'2026-07-01','value':'33000'}],'erp':[{'date':'2026-11-23','erp':'.0581'}]}
    source=tmp_path/'us-snapshot.json';original=json.dumps(snapshot).encode();source.write_bytes(original)
    points=extract('us_barometer',tmp_path)
    assert {p['series'] for p in points}=={'wilshire','gdp','erp','buffett'}
    assert source.read_bytes()==original
    with db_factory() as db:
        save_points(db,seed['editor'].team_id,'us_barometer',points)
        proxy=db.scalar(select(BriefDataPoint).where(BriefDataPoint.series=='buffett'))
        assert proxy.quality_status=='unverified' and proxy.values['pct']=='218.54'
        assert proxy.units['wilshire_index_points']=='index_points'
