"""Fetch, validate and render the US barometer brief (Wilshire 5000 + StreetStats ERP)."""
from __future__ import annotations

import csv
import io
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


def history_csv(snapshot, metric):
    """Export the exact calculated history saved with one brief edition."""
    if metric == 'buffett':
        rows = buffett_series(snapshot['wilshire'], snapshot['gdp'])
    elif metric == 'erp':
        rows = erp_series(snapshot['erp'])
    else:
        raise ValueError('未知的历史指标')
    target = snapshot['target']
    rows = [row for row in rows if row['date'] <= target]
    if not rows or rows[-1]['date'] != target:
        raise ValueError('所选版本的历史数据与数据日不符')
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(('日期', '指标值（%）'))
    writer.writerows((row['date'], str(row['pct'])) for row in rows)
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def window(rows, day, years=10):
    end = date.fromisoformat(day)
    try:
        start = end.replace(year=end.year - years)
    except ValueError:
        start = end.replace(year=end.year - years, day=28)
    return [row for row in rows if start.isoformat() <= row['date'] <= end.isoformat()]


def fetch(day, out,*,allow_gap_repair=False,on_series=None):
    record(out, 2, '正在获取 Wilshire 5000、FRED 季度GDP 与 StreetStats 股权风险溢价')
    from app.briefs.failures import SourceUnavailable,SourceContractError
    from app.briefs.sources import save
    import httpx
    collected = {}; errors = []
    for name, loader in (('wilshire',fetch_data.wilshire),('gdp',fetch_data.gdp),('erp',fetch_data.streetstats_erp)):
        try:
            with fetch_data.capture(out):
                rows = loader()
        except (ValueError,KeyError,TypeError,OSError,httpx.HTTPError) as exc:
            # Capture remaining independent sources even when one provider fails.
            errors.append({'source':name,'type':type(exc).__name__,
                           'reason':str(exc)[:300] if isinstance(exc,SourceUnavailable) else '来源结构或数值校验失败'})
            continue
        collected[name] = rows
        if on_series:on_series(name,rows)
    if errors:
        save(out/'source-failures.json',errors)
        names=','.join(item['source'] for item in errors)
        problem = SourceUnavailable if all(item['type']=='SourceUnavailable' for item in errors) else SourceContractError
        raise problem('美股晴雨表采集或解析未完成：'+names+'；请对照原始响应排查程序，其他成功序列已归档',source=names)
    wilshire_rows,gdp_rows,erp_rows = (collected[name] for name in ('wilshire','gdp','erp'))
    for label, rows in (('美股总市值', wilshire_rows), ('股权风险溢价', erp_rows)):
        if not allow_gap_repair and not any(row['date'] == day for row in rows):
            raise ValueError(f'{label}目标日期尚未更新')
    from app.briefs.policy import CHINA, expected_date
    from datetime import datetime
    unverified=date.fromisoformat(day)<expected_date('us_barometer',datetime.now(CHINA).date())
    return {'target': day, 'wilshire': window(wilshire_rows, day), 'gdp': gdp_rows,
            'historical_vintage_unverified':unverified,'gdp_vintage_policy':'current_revised',
            'erp': window(erp_rows, day), 'sources': dict(SOURCES)}


def render(snapshot, out,*,draw=True):
    day = snapshot['target']
    buffett = buffett_series(snapshot['wilshire'], snapshot['gdp'])
    erp = erp_series(snapshot['erp'])
    today_buffett = next((row for row in buffett if row['date'] == day), None)
    today_erp = next((row for row in erp if row['date'] == day), None)
    from app.briefs.trading_calendar import previous_session
    from app.briefs.failures import MissingObservations
    previous_day = previous_session('xnys', day).isoformat()
    for name, rows in (('巴菲特指数（Wilshire/GDP）', buffett), ('股权风险溢价（ERP）', erp)):
        available = {row['date'] for row in rows}
        missing = sorted({day, previous_day} - available)
        if missing:
            latest = max(available) if available else None
            label='前一交易日' if previous_day in missing else '目标日期'
            raise MissingObservations(name+label+'缺少必要交易日：'+','.join(missing),
                                      source=name, missing_dates=missing, latest_date=latest)
    previous_buffett = next((row for row in buffett if row['date'] == previous_day), None)
    previous_erp = next((row for row in erp if row['date'] == previous_day), None)
    copy = verdict_copy(day, today_buffett['pct'], today_erp['pct'],
                        previous_buffett['pct'], previous_erp['pct'])
    quarter = gdp_asof(snapshot['gdp'], day)
    from app.briefs.charts import us_barometer_chart
    record(out, 3, '指标与分级已校验，正在绘制双面板合成图')
    chart = us_barometer_chart(buffett, erp, out) if draw else None
    return {
        'title': copy['title'], 'dynamic_body': copy['body'],
        'images': [chart.name] if chart else [], 'data_date': day,
        'facts': {
            'buffett_pct': str(today_buffett['pct']), 'buffett_grade': copy['buffett']['grade'],
            'erp_pct': str(today_erp['pct']), 'erp_grade': copy['erp']['grade'],
            'previous_date': previous_day,
            'buffett_previous_pct': str(previous_buffett['pct']),
            'erp_previous_pct': str(previous_erp['pct']),
            'verdict': copy['label'], 'score': copy['score'],
            'gdp_period': quarter['period'], 'gdp_value': str(quarter['value']),
            'sources': dict(SOURCES),
        },
        'warnings': ['GDP使用当前已公布修订值；历史曲线不是当时可得GDP版本回放'],
    }
