"""Exportable US proxy observations, without changing production calculations."""
from decimal import Decimal
from sqlalchemy import select
from app.db.models import BriefDataPoint

METHOD='legacy_wilshire_points_over_gdp_proxy_v1'
QUALITY_REASON='Wilshire指数点位不等同美元市值，历史代理计算口径未核验'


def us_buffett_points(wilshire,gdp):
    from .engines.us_barometer.calculate import buffett_series,gdp_asof
    from .engines.us_barometer.run import SOURCES
    gdp=sorted(gdp,key=lambda row:row['period'])
    if not gdp:return []
    markets={row['date']:row for row in wilshire if row['date']>=gdp[0]['period']}
    result=[]
    for row in buffett_series([markets[day] for day in sorted(markets)],gdp):
        quarter=gdp_asof(gdp,row['date'])
        n,d=Decimal(str(markets[row['date']]['close'])),Decimal(str(quarter['value']))
        if not n.is_finite() or not d.is_finite() or n<=0 or d<=0:
            raise ValueError('巴菲特历史导出输入须为有限正数')
        result.append({'series':'buffett','date':row['date'],'method':METHOD,
            'quality_status':'unverified','quality_reason':QUALITY_REASON,
            'values':{**row,'ratio':str(n/d),'wilshire_index_points':str(n),
                'gdp_usd_billion':str(d),'calculation_status':'index_point_proxy_unverified',
                'gdp_period':quarter['period'],'gdp_vintage_policy':'current_revised_not_point_in_time',
                'market_source':SOURCES['wilshire'],'gdp_source':SOURCES['gdp']}})
    return result


def backfill_us_buffett(db,team_id,*,end=None):
    """Append stored-input history once; never fetch or modify editorial editions."""
    from .data_reliability import save_points
    query=select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,
        BriefDataPoint.category=='us_barometer',BriefDataPoint.quality_status=='accepted',
        BriefDataPoint.series.in_(('wilshire','gdp'))).order_by(
        BriefDataPoint.collected_at.desc(),BriefDataPoint.revision.desc())
    inputs={'wilshire':{},'gdp':{}}
    for row in db.scalars(query):
        expected='yahoo_w5000_v1' if row.series=='wilshire' else 'fred_current_revised_not_point_in_time'
        if row.method!=expected or end is not None and row.data_date>end:continue
        inputs[row.series].setdefault(row.data_date.isoformat(),row)
    if not all(inputs.values()):return 0
    gdp=[row.values for _,row in sorted(inputs['gdp'].items())]
    points=us_buffett_points([row.values for _,row in sorted(inputs['wilshire'].items())],gdp)
    for point in points:
        values=point['values'];market=inputs['wilshire'][point['date']]
        denominator=inputs['gdp'][values['gdp_period']]
        values.update(source_status='derived_from_stored_inputs',
            market_source_snapshot_id=str(market.source_snapshot_id) if market.source_snapshot_id else None,
            gdp_source_snapshot_id=str(denominator.source_snapshot_id) if denominator.source_snapshot_id else None)
    return save_points(db,team_id,'us_barometer',points)
