"""Reproduce the two Legulegu turnover-ratio panels from daily counts.

Usage: python build_turnover.py --data daily.json --reference chart-options.json
The reference argument checks every date, index close and rounded MA9 value.
No image generation, tracing, curve fitting, extra smoothing or resampling.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

SOURCE = 'https://legulegu.com/stockdata/market-turn-over-ratio-statistics'
SCRIPT_SOURCE = 'https://legulegu.com/static/js/lg-charts-turn-over-ratio.js?date=20260720v2'
Q = Decimal('0.01')


def q2(value: Decimal) -> Decimal:
    return value.quantize(Q, rounding=ROUND_HALF_UP)


def load_and_calculate(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding='utf-8'), parse_float=Decimal)
    if not isinstance(raw, list):
        raise ValueError('Expected a list of raw daily count records')
    if len(raw) < 10:
        raise ValueError('At least 10 daily records are required for MA9 and its daily change')
    dates = [row['date'] for row in raw]
    if dates != sorted(set(dates)):
        raise ValueError('Source dates must be strictly ascending and unique')
    for row in raw:
        date.fromisoformat(row['date'])
        total = row['totalCompany']
        if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
            raise ValueError(f"Invalid totalCompany for {row['date']}")
        if any(not isinstance(row[k], int) or isinstance(row[k], bool) for k in ('belowPercent3', 'belowPercent5')):
            raise ValueError(f"Invalid turnover counts for {row['date']}")
        if not 0 <= row['belowPercent3'] <= row['belowPercent5'] <= total:
            raise ValueError(f"Invalid turnover counts for {row['date']}")
        close = Decimal(str(row['close']))
        if not close.is_finite() or close <= 0:
            raise ValueError(f"Invalid index close for {row['date']}")
        row['low_daily'] = Decimal(row['belowPercent3']) / total * 100
        row['high_daily'] = Decimal(total - row['belowPercent5']) / total * 100
    calculated = []
    for i in range(8, len(raw)):
        window = raw[i-8:i+1]
        row = raw[i]
        calculated.append({
            'date': row['date'],
            'low_ma9': q2(sum((r['low_daily'] for r in window), Decimal(0))/9),
            'high_ma9': q2(sum((r['high_daily'] for r in window), Decimal(0))/9),
            'index_close': Decimal(row['close']),
            'low_daily': q2(row['low_daily']),
            'high_daily': q2(row['high_daily']),
        })
    return calculated


def verify_reference(rows: list[dict], reference: Path | None) -> dict:
    if reference is None:
        return {'source_chart_comparison': 'not provided'}
    charts = json.loads(reference.read_text(encoding='utf-8'), parse_float=Decimal)
    expected_dates = [r['date'] for r in rows]
    for chart, field, name, threshold, inverse in [
        (charts[0], 'low_ma9', '低于3%', 96, True),
        (charts[1], 'high_ma9', '高于5%', 17, False),
    ]:
        axis = chart['xAxis']
        if isinstance(axis, list):
            axis = axis[0]
        actual_dates = [date(*map(int, s.split('/'))).isoformat() for s in axis['data']]
        if actual_dates != expected_dates:
            raise AssertionError('Date mismatch against source chart')
        series = {s['name']: s for s in chart['series']}
        if [Decimal(str(x)) for x in series[name]['data']] != [r[field] for r in rows]:
            raise AssertionError(f'{name}: MA9 differs from source chart')
        if [Decimal(str(x)) for x in series['上证指数']['data']] != [r['index_close'] for r in rows]:
            raise AssertionError('Index close differs from source chart')
        if series[name]['markLine']['data'][0]['yAxis'] != threshold:
            raise AssertionError('Unexpected source threshold')
        if bool(chart['yAxis'][0].get('inverse', False)) != inverse:
            raise AssertionError('Unexpected source axis direction')
    return {'source_chart_comparison': 'exact match', 'verified_points_per_series': len(rows),
            'date_mismatches': 0, 'ratio_value_mismatches': 0, 'index_value_mismatches': 0}


def build_outputs(data: Path, out_dir: Path, reference: Path | None = None,
                  start: str = '2015-01-01', as_of: str | None = None) -> dict:
    all_rows = load_and_calculate(data)
    audit = verify_reference(all_rows, reference)
    # Calculate the full MA9 history BEFORE applying the display date filter.
    rows = [r for r in all_rows if r['date'] >= start and (not as_of or r['date'] <= as_of)]
    if len(rows) < 2:
        raise ValueError('At least two display rows required')
    current, previous = rows[-1], rows[-2]
    out = out_dir
    out.mkdir(parents=True, exist_ok=True)
    from render_template import render_template
    png, threshold_geometry, index_max, template_audit = render_template(rows, out)
    n = len(rows)
    low_delta = current['low_ma9']-previous['low_ma9']
    high_delta = current['high_ma9']-previous['high_ma9']
    from copy_tracks import build_facts, generate_rule_copy
    copy = generate_rule_copy(build_facts(current, previous))
    title, body = copy['title'], copy['body']
    md = f'# {title}\n\n{body}\n\n[数据来源：乐咕乐股]({SOURCE})\n'
    (out/'快讯文案.md').write_text(md,encoding='utf-8')
    with (out/'绘图数据.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['date','low_ma9','high_ma9','index_close','low_daily','high_daily'])
        writer.writeheader();writer.writerows(rows)
    audit.update({'source':SOURCE,'source_script':SCRIPT_SOURCE,'display_points':n,'display_start':rows[0]['date'],
                  'display_end':current['date'],'left_axis_low':[100,0],'left_axis_high':[0,100],
                  'index_axis_both':[0,index_max],'thresholds':[96,17],'shared_dates_and_ticks':True,
                  'rendered_threshold_geometry':threshold_geometry,'extra_smoothing':False,'resampling':False,
                  'latest':{k:str(v) for k,v in current.items()},'change_pp':{'low':str(low_delta),'high':str(high_delta)},
                  'layout_revision':'v3: preserve original supplied template pixels; replace curves and dates only'})
    audit.update(template_audit)
    (out/'verification.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'out':str(out),'png':str(png),'text':body,'audit':audit,
            'current':current,'previous':previous}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--start', default='2015-01-01')
    parser.add_argument('--as-of')
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    result = build_outputs(args.data, args.out_dir, args.reference, args.start, args.as_of)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))


if __name__ == '__main__':
    main()
