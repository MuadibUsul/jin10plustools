"""Fetch, validate and render the US barometer brief (Wilshire 5000 + StreetStats ERP)."""
from __future__ import annotations

from datetime import date

from app.briefs.progress import record

from . import fetch_data
from .calculate import buffett_series, erp_series, gdp_asof, verdict_copy

SNAPSHOT = 'us-snapshot.json'
SOURCES = {
    'wilshire': 'https://query1.finance.yahoo.com/v8/finance/chart/%5EW5000',
    'gdp': 'https://fred.stlouisfed.org/series/GDP',
    'erp': 'https://streetstats.finance/valuation/market',
}


def window(rows, day, years=10):
    end = date.fromisoformat(day)
    try:
        start = end.replace(year=end.year - years)
    except ValueError:
        start = end.replace(year=end.year - years, day=28)
    return [row for row in rows if start.isoformat() <= row['date'] <= end.isoformat()]


def fetch(day, out,*,allow_gap_repair=False,on_series=None):
    record(out, 2, '正在获取 Wilshire 5000、FRED 季度GDP 与 StreetStats 股权风险溢价')
    wilshire_rows = fetch_data.wilshire()
    if on_series:on_series('wilshire',wilshire_rows)
    gdp_rows = fetch_data.gdp()
    if on_series:on_series('gdp',gdp_rows)
    erp_rows = fetch_data.streetstats_erp()
    if on_series:on_series('erp',erp_rows)
    for label, rows in (('美股总市值', wilshire_rows), ('股权风险溢价', erp_rows)):
        if not allow_gap_repair and not any(row['date'] == day for row in rows):
            raise ValueError(f'{label}目标日期尚未更新')
    from app.briefs.policy import CHINA, expected_date
    from datetime import datetime
    unverified=date.fromisoformat(day)<expected_date('us_barometer',datetime.now(CHINA).date())
    return {'target': day, 'wilshire': window(wilshire_rows, day), 'gdp': gdp_rows,
            'historical_vintage_unverified':unverified,'gdp_vintage_policy':'current_revised',
            'erp': window(erp_rows, day), 'sources': dict(SOURCES)}


def render(snapshot, out):
    day = snapshot['target']
    buffett = buffett_series(snapshot['wilshire'], snapshot['gdp'])
    erp = erp_series(snapshot['erp'])
    today_buffett = next((row for row in buffett if row['date'] == day), None)
    today_erp = next((row for row in erp if row['date'] == day), None)
    if today_buffett is None or today_erp is None:
        raise ValueError('目标日期的指标数据缺失')
    copy = verdict_copy(day, today_buffett['pct'], today_erp['pct'])
    quarter = gdp_asof(snapshot['gdp'], day)
    from app.briefs.charts import us_barometer_chart
    record(out, 3, '指标与分级已校验，正在绘制双面板合成图')
    chart = us_barometer_chart(buffett, erp, out)
    return {
        'title': copy['title'], 'dynamic_body': copy['body'],
        'images': [chart.name], 'data_date': day,
        'facts': {
            'buffett_pct': str(today_buffett['pct']), 'buffett_grade': copy['buffett']['grade'],
            'erp_pct': str(today_erp['pct']), 'erp_grade': copy['erp']['grade'],
            'verdict': copy['label'], 'score': copy['score'],
            'gdp_period': quarter['period'], 'gdp_value': str(quarter['value']),
            'sources': dict(SOURCES),
        },
        'warnings': ['GDP使用当前已公布修订值；历史曲线不是当时可得GDP版本回放'],
    }
