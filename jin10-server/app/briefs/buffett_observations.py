"""Materialize US Buffett history from already stored, dated source observations."""
from datetime import date
from sqlalchemy import select
from app.db.models import BriefDataPoint
from .data_reliability import save_points
from .engines.us_barometer.calculate import buffett_series, gdp_asof
from .engines.us_barometer.run import SOURCES

METHOD='legacy_wilshire_points_over_gdp_proxy_v1'


def backfill_us_buffett(db,team_id,*,end=None):
    query=select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,
        BriefDataPoint.category=='us_barometer',BriefDataPoint.quality_status=='accepted',
        BriefDataPoint.series.in_(('wilshire','gdp'))).order_by(
        BriefDataPoint.collected_at.desc(),BriefDataPoint.revision.desc())
    inputs={'wilshire':{},'gdp':{}}
    for row in db.scalars(query):
        expected='yahoo_w5000_v1' if row.series=='wilshire' else 'fred_current_revised_not_point_in_time'
        if row.method!=expected:continue
        if end is not None and row.data_date>end:continue
        inputs[row.series].setdefault(row.data_date.isoformat(),row)
    if not all(inputs.values()):return 0
    gdp=[point.values for _,point in sorted(inputs['gdp'].items())]
    wilshire=[point.values for day,point in sorted(inputs['wilshire'].items()) if day>=gdp[0]['period']]
    points=[]
    for row in buffett_series(wilshire,gdp):
        market=inputs['wilshire'][row['date']]
        period=gdp_asof(gdp,row['date'])['period'];denominator=inputs['gdp'][period]
        values={**row,'market_source':SOURCES['wilshire'],'gdp_source':SOURCES['gdp'],
            'source_status':'derived_from_stored_inputs',
            'market_source_snapshot_id':str(market.source_snapshot_id) if market.source_snapshot_id else None,
            'gdp_source_snapshot_id':str(denominator.source_snapshot_id) if denominator.source_snapshot_id else None}
        points.append({'series':'buffett','date':row['date'],'method':METHOD,'values':values})
    return save_points(db,team_id,'us_barometer',points)
