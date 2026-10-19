"""Market-structure metrics and wording: index changes, equal-weight gap, small caps."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

Q = Decimal('0.01')


def q2(value):
    return Decimal(str(value)).quantize(Q, rounding=ROUND_HALF_UP)


def day_index(rows, day):
    return next((index for index, row in enumerate(rows) if row['date'] == day), None)


def change(rows, day):
    index = day_index(rows, day)
    if index is None or index == 0:
        raise ValueError('目标日期的指数数据缺失')
    return (Decimal(str(rows[index]['close'])) / Decimal(str(rows[index - 1]['close'])) - 1) * 100


def gap_series(data):
    """Equal-weight S&P 500 index minus the cap-weighted index, in index points."""
    closes = {row['date']: row['close'] for row in data['spx']}
    return [{'date': row['date'], 'value': q2(Decimal(str(row['close'])) - Decimal(str(closes[row['date']])))}
            for row in data['spxew'] if row['date'] in closes]


def structure(day, data):
    target = date.fromisoformat(day)
    changes = {name: change(data[name], day) for name in ('spx', 'ndx', 'dji', 'rut')}
    gap = [row for row in gap_series(data) if row['date'] <= day]
    index = day_index(gap, day)
    if index is None or len(gap) < 60:
        raise ValueError('等权指数目标日期尚未更新或历史不足')
    year = target - timedelta(days=365)
    window = [row for row in gap if row['date'] >= year.isoformat()]
    low = min(window, key=lambda row: row['value'])
    high = max(window, key=lambda row: row['value'])
    current = gap[index]
    previous = gap[index - 1]
    span = Decimal(str(high['value'])) - Decimal(str(low['value']))
    percentile = float((Decimal(str(current['value'])) - Decimal(str(low['value']))) / span) if span else 0.0
    gap_change = Decimal(str(current['value'])) - Decimal(str(previous['value']))
    gap_up = gap_change > 0
    spx_up = changes['spx'] > 0

    levels = ['仍处于年内偏低水平', '处于年内中间水平', '已回到年内偏高水平']
    level = levels[0] if percentile < .35 else levels[1] if percentile < .65 else levels[2]
    if gap_up and percentile < .5:
        level = '但' + level
    improve = '市场宽度改善程度有限' if percentile < .4 else '市场宽度有所修复' if percentile < .7 else '市场宽度明显改善'
    verdict = '市场宽度依然不佳' if percentile < .35 else '市场宽度中性' if percentile < .65 else '市场宽度有所改善'

    three_values = (changes['spx'], changes['ndx'], changes['dji'])
    three = ('收跌' if all(value < 0 for value in three_values)
             else '收涨' if all(value > 0 for value in three_values) else '涨跌互现')
    low_month = int(low['date'][5:7])
    if low_month == target.month and gap_up:
        rebound = '从本月刷新的近一年低点反弹'
    elif gap_up:
        rebound = '从近一年低位回升'
    else:
        rebound = '较前一日回落'
    weight = ('暗示权重股承受压力更大' if gap_up and not spx_up
              else '暗示权重股相对抗跌' if not gap_up and spx_up else '权重股与整体走势大体同步')

    rut = data['rut']
    rut_index = day_index(rut, day)
    if rut_index is None:
        raise ValueError('罗素2000目标日期尚未更新')
    today = rut[rut_index]
    lower = [row for row in rut[:rut_index] if row['low'] < today['low']]
    low_clause = f'，一度刷新{int(lower[-1]["date"][5:7])}月以来新低' if lower else '，维持在阶段高位'
    peak = max(row['close'] for row in rut[:rut_index + 1])
    drawdown = (Decimal(str(today['close'])) / Decimal(str(peak)) - 1) * 100
    drawdown_clause = (f'，从历史高点已经回调了{abs(drawdown):.0f}%' if drawdown < 0
                       else '，仍处于历史高点附近')
    three_min = min(changes['spx'], changes['ndx'], changes['dji'])
    three_max = max(changes['spx'], changes['ndx'], changes['dji'])
    if changes['rut'] < three_min:
        compare, conclusion = '比三大股指跌幅明显更大', '表明资金持续从小盘股中流出'
    elif changes['rut'] > three_max:
        compare, conclusion = '表现强于三大股指', '小盘股资金面有所修复'
    else:
        compare, conclusion = '与三大股指表现接近', '小盘股资金面未见明显改善'
    route = '收跌' if changes['rut'] < 0 else '收涨'

    lead_small = '小盘股率先进入调整区间' if drawdown <= -10 else '小盘股仍在高位区间'
    lead_mega = '但巨头也开始承压' if gap_up and not spx_up else '但权重股仍相对强势'
    body = (f'美股市场结构：{lead_small}，{lead_mega}。\n'
            f'1、昨日三大股指{three}，标普500等权指数与市值加权指数之差{rebound}，{weight}，{level}，{improve}；\n'
            f'2、代表小盘股的罗素2000指数也{route}{abs(changes["rut"]):.2f}%{low_clause}{drawdown_clause}，{compare}，{conclusion}。')
    return {
        'title': f'【美股晴雨表】美股{target.month}月{target.day}日市场结构：{verdict}',
        'body': body, 'verdict': verdict, 'gap_rows': window,
        'facts': {
            'spx_change': str(q2(changes['spx'])), 'ndx_change': str(q2(changes['ndx'])),
            'dji_change': str(q2(changes['dji'])), 'rut_change': str(q2(changes['rut'])),
            'gap': str(current['value']), 'gap_percentile': f'{percentile:.3f}',
            'gap_low': str(low['value']), 'gap_low_date': low['date'],
            'rut_drawdown': str(q2(drawdown)), 'verdict': verdict,
        },
    }
