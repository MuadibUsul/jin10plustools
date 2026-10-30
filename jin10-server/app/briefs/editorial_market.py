"""Pure editorial contexts for Token CPI and the two US-market briefs.

No observations, images or legacy wording are mutated. Each exposed slot is a
complete fact statement; a module selector may arrange slots but not split them.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re


def _day(raw, reason='invalid_date') -> date:
    try:
        value = date.fromisoformat(raw)
        if value.isoformat() != raw:
            raise ValueError
        return value
    except (TypeError, ValueError):
        raise ValueError(reason) from None


def _num(raw, reason='invalid_number', optional=False) -> Decimal | None:
    if optional and raw in (None, ''):
        return None
    if isinstance(raw, bool) or not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', str(raw).strip()):
        raise ValueError(reason)
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise ValueError(reason) from None
    if not value.is_finite():
        raise ValueError(reason)
    return value


def _unsigned(raw) -> str:
    return str(raw).strip().lstrip('+-')


def _md(value: date) -> str:
    return f'{value.month}月{value.day}日'


def _move(raw, *, close=False) -> str:
    value = _num(raw)
    if value == 0:
        return ('收平' if close else '持平') + f'（{_unsigned(raw)}%）'
    positive = '收涨' if close else '上涨'
    negative = '收跌' if close else '下跌'
    return (positive if value > 0 else negative) + _unsigned(raw) + '%'


def _token(payload: dict, actual: date, facts: dict) -> dict:
    price = _num(facts.get('token_price'), 'invalid_price')
    if price <= 0:
        raise ValueError('invalid_price')
    weekly = _num(facts.get('weekly_change_pct'), 'invalid_change', optional=True)
    if weekly is not None and weekly <= -100:
        raise ValueError('invalid_change')
    previous = _day(facts['previous_date'], 'invalid_baseline') if facts.get('previous_date') else None
    previous_price = _num(facts.get('previous_price'), 'invalid_baseline', optional=True)
    if previous and (actual - previous).days != 7:
        raise ValueError('invalid_baseline')
    if previous_price is not None and previous_price <= 0:
        raise ValueError('invalid_baseline')
    origin = facts.get('weekly_source') or 'calculated'
    if origin not in {'calculated', 'public_visible_summary', 'manual_entry'}:
        raise ValueError('source_provenance_missing')
    if weekly is not None and origin == 'calculated' and (not previous or previous_price is None):
        raise ValueError('invalid_baseline')
    if weekly is not None and origin == 'calculated':
        expected_change = (price / previous_price - 1) * 100
        if abs(expected_change - weekly) > Decimal('0.02'):
            raise ValueError('invalid_change')
    precision = facts.get('source_precision')
    if precision is not None and (isinstance(precision, bool) or not isinstance(precision, int)
                                  or not 0 <= precision <= 6):
        raise ValueError('invalid_precision')
    printed_places = len(str(facts['token_price']).partition('.')[2])
    if precision is not None and printed_places > precision:
        raise ValueError('invalid_precision')

    primary = f'Token支出价格指数报{facts["token_price"]}美元/百万Token'
    if weekly is None:
        primary += '，七日变化基准缺失。'
    elif origin == 'public_visible_summary':
        primary += f'，一周{_move(facts["weekly_change_pct"])}（官网展示值）。'
    elif origin == 'manual_entry':
        primary += f'，一周{_move(facts["weekly_change_pct"])}（人工录入）。'
    else:
        primary += f'，较{_md(previous)}的{facts["previous_price"]}美元/百万Token{_move(facts["weekly_change_pct"])}。'

    relation = facts.get('peak_relation') or 'unavailable'
    peak = _num(facts.get('prior_peak_price'), 'peak_mismatch', optional=True)
    peak_day = _day(facts['prior_peak_date'], 'peak_mismatch') if facts.get('prior_peak_date') else None
    drawdown = _num(facts.get('drawdown_pct'), 'peak_mismatch', optional=True)
    peak_fact = ''
    if relation not in {'above', 'tie', 'below', 'unavailable'}:
        raise ValueError('peak_mismatch')
    if relation != 'unavailable':
        if peak is None or peak <= 0 or peak_day is None or peak_day >= actual:
            raise ValueError('peak_mismatch')
        expected = 'above' if price > peak else 'below' if price < peak else 'tie'
        if relation != expected:
            raise ValueError('peak_mismatch')
        located = f'{_md(peak_day)}的此前已收录高点{facts["prior_peak_price"]}美元/百万Token'
        if relation == 'below':
            if drawdown is None or not 0 <= drawdown < 100:
                raise ValueError('peak_mismatch')
            if abs((1 - price / peak) * 100 - drawdown) > Decimal('0.02'):
                raise ValueError('peak_mismatch')
            peak_fact = f'较{located}，当前累计回落{facts["drawdown_pct"]}%。'
        elif relation == 'tie':
            if drawdown is not None:
                raise ValueError('peak_mismatch')
            peak_fact = f'该价格与{located}持平，属于并列高位。'
        else:
            if drawdown is not None:
                raise ValueError('peak_mismatch')
            peak_fact = f'该价格高于{located}。'
    elif any(value is not None for value in (peak, peak_day, drawdown)):
        raise ValueError('peak_mismatch')

    count_raw = facts.get('range_count', 0)
    count = _num(count_raw, 'invalid_range')
    if count != count.to_integral_value() or not 0 <= count <= 7:
        raise ValueError('invalid_range')
    low = _num(facts.get('range_low'), 'invalid_range', optional=True)
    high = _num(facts.get('range_high'), 'invalid_range', optional=True)
    if (low is None) != (high is None) or (low is not None and (low <= 0 or low > price or price > high)):
        raise ValueError('invalid_range')
    range_fact = ''
    narrow = facts.get('tail_narrowed') is True
    if narrow:
        if origin != 'calculated':
            raise ValueError('invalid_range')
        start = _day(facts.get('tail_start'), 'invalid_range')
        tail_low, tail_high = (_num(facts.get(key), 'invalid_range') for key in ('tail_low', 'tail_high'))
        if ((actual - start).days != 4 or not 0 < tail_low <= price <= tail_high
                or tail_high - tail_low > price * Decimal('0.01')):
            raise ValueError('invalid_range')
        range_fact = (f'{_md(start)}至{_md(actual)}，指数介于{facts["tail_low"]}—{facts["tail_high"]}'
                      '美元/百万Token；按已校验的前后五日比较，短期波动收窄。')
    elif count >= 5:
        if low is None or high is None:
            raise ValueError('invalid_range')
        range_fact = f'最近七日已收录价格介于{facts["range_low"]}—{facts["range_high"]}美元/百万Token。'

    low_position = relation == 'below' and drawdown is not None and drawdown >= 40
    if relation in {'tie', 'above'}:
        scenario = 'peak_' + relation
    elif weekly is None:
        scenario = 'missing_week'
    elif weekly > 0 and drawdown is not None and drawdown >= 50:
        scenario = 'halved_rebound'
    elif weekly > 0:
        scenario = 'low_rebound' if low_position else 'rebound'
    elif weekly == 0:
        scenario = 'low_flat' if low_position else 'flat'
    else:
        scenario = 'low_decline' if low_position else 'decline'
    if narrow and relation not in {'tie', 'above'} and weekly is not None:
        scenario = 'narrow_' + ('low' if low_position else 'normal')
    return {'scenario': scenario,
            'slots': {'data_day': _md(actual), 'primary': primary, 'range': range_fact, 'peak': peak_fact},
            'bindings': {key: facts.get(key) for key in ('token_price', 'weekly_change_pct', 'previous_date',
                        'previous_price', 'source_precision', 'weekly_source', 'prior_peak_price',
                        'prior_peak_date', 'peak_relation', 'drawdown_pct', 'range_low', 'range_high',
                        'range_count', 'tail_narrowed', 'tail_start', 'tail_low', 'tail_high')},
            'writing_order': '先数据日及Token价格和周变化，再给有依据的短期区间，最后交代已收录高点关系。'}


def _barometer(payload: dict, actual: date, facts: dict) -> dict:
    from .engines.us_barometer.calculate import grade, overall, BUFFETT_GRADES, ERP_GRADES

    buffett = _num(facts.get('buffett_pct'))
    erp = _num(facts.get('erp_pct'))
    if buffett < 0 or erp < 0:
        raise ValueError('invalid_number')
    buffett_grade = grade(buffett, BUFFETT_GRADES)
    erp_grade = grade(erp, ERP_GRADES)
    verdict, score = overall(buffett_grade['score'], erp_grade['score'])
    if (facts.get('buffett_grade') != buffett_grade['label']
            or facts.get('erp_grade') != erp_grade['label'] or facts.get('verdict') != verdict
            or _num(facts.get('score'), 'grade_mismatch') != score):
        raise ValueError('grade_mismatch')
    if facts.get('gdp_period'):
        if _day(facts['gdp_period']) > actual:
            raise ValueError('invalid_gdp_period')
    if facts.get('gdp_value') is not None and _num(facts['gdp_value']) <= 0:
        raise ValueError('invalid_gdp_value')
    scenarios = {'高': 'high', '较高': 'relatively_high', '中性': 'neutral', '较低': 'relatively_low', '低': 'low'}
    return {'scenario': scenarios[verdict],
            'slots': {'data_day': _md(actual),
                      'buffett': f'巴菲特指数为{facts["buffett_pct"]}%，按本栏目阈值处于{buffett_grade["label"]}区间。',
                      'erp': f'标普500指数股权风险溢价（ERP）为{facts["erp_pct"]}%，分级为{erp_grade["label"]}。',
                      'verdict': f'两项指标合并后的美股投资性价比分级为{verdict}。'},
            'bindings': {key: facts.get(key) for key in ('buffett_pct', 'buffett_grade', 'erp_pct', 'erp_grade',
                                                        'verdict', 'score', 'gdp_period', 'gdp_value')},
            'writing_order': '先巴菲特指数及其分级，再给ERP与分级，最后交代本栏目综合性价比分级；不新增交易建议。'}


def _structure(payload: dict, actual: date, facts: dict) -> dict:
    changes = {key: _num(facts.get(key + '_change'), 'invalid_change') for key in ('ndx', 'spx', 'dji', 'rut')}
    if any(value <= -100 for value in changes.values()):
        raise ValueError('invalid_change')
    gap = _num(facts.get('gap'))
    low = _num(facts.get('gap_low'))
    percentile = _num(facts.get('gap_percentile'))
    low_day = _day(facts.get('gap_low_date'))
    drawdown = _num(facts.get('rut_drawdown'))
    if not 0 <= percentile <= 1 or gap < low or low_day > actual or (actual - low_day).days > 365:
        raise ValueError('invalid_breadth')
    if not -100 < drawdown <= 0:
        raise ValueError('invalid_drawdown')
    verdict = facts.get('verdict')
    labels = {'市场宽度依然不佳': 'breadth_weak', '市场宽度中性': 'breadth_neutral',
              '市场宽度有所改善': 'breadth_improving'}
    if verdict not in labels:
        raise ValueError('grade_mismatch')
    # The source rounds percentile to three places. Near a boundary keep the
    # source's validated verdict instead of changing it using rounded precision.
    expected = ('市场宽度依然不佳' if percentile < Decimal('0.35') else
                '市场宽度中性' if percentile < Decimal('0.65') else '市场宽度有所改善')
    at_boundary = min(abs(percentile - Decimal('0.35')), abs(percentile - Decimal('0.65'))) <= Decimal('0.0005')
    if verdict != expected and not at_boundary:
        raise ValueError('grade_mismatch')
    indices = ('前一个交易日纳斯达克指数' + _move(facts['ndx_change'], close=True)
               + '、标普500指数' + _move(facts['spx_change'], close=True)
               + '、道琼斯指数' + _move(facts['dji_change'], close=True) + f'（{_md(actual)}）')
    comparison = ('表现弱于三大股指' if changes['rut'] < min(changes[key] for key in ('ndx', 'spx', 'dji')) else
                  '表现强于三大股指' if changes['rut'] > max(changes[key] for key in ('ndx', 'spx', 'dji')) else
                  '表现介于三大股指之间')
    # The existing column deliberately omits the absolute gap because providers
    # have different closes. Retain that publication boundary in the new copy.
    level = ('偏低' if verdict == '市场宽度依然不佳' else
             '中间' if verdict == '市场宽度中性' else '偏高')
    path = ''
    if facts.get('gap_previous') is not None or facts.get('gap_change') is not None:
        previous_gap = _num(facts.get('gap_previous'), 'invalid_breadth')
        gap_change = _num(facts.get('gap_change'), 'invalid_breadth')
        if abs(gap - previous_gap - gap_change) > Decimal('0.01'):
            raise ValueError('invalid_breadth')
        path = ('较前一交易日回升，' if gap_change > 0 else
                '较前一交易日回落，' if gap_change < 0 else '与前一交易日持平，')
    breadth = f'标普500等权指数与市值加权指数之差{path}处于近一年已收录区间的{level}水平。'
    low_clause = ''
    if facts.get('rut_low_since_date'):
        since = _day(facts['rut_low_since_date'], 'invalid_low_since')
        month = _num(facts.get('rut_low_since_month'), 'invalid_low_since')
        if since >= actual or month != since.month:
            raise ValueError('invalid_low_since')
        located = _md(since) if since.year == actual.year else f'{since.year}年{_md(since)}'
        low_clause = f'，盘中低点处于{located}以来低位'
    elif facts.get('rut_low_since_month') is not None:
        raise ValueError('invalid_low_since')
    drawdown_text = _unsigned(facts['rut_drawdown'])
    approximate = ''
    if facts.get('rut_drawdown_rounded_pct') is not None:
        rounded = _num(facts['rut_drawdown_rounded_pct'], 'invalid_drawdown')
        if rounded < 0 or abs(rounded - abs(drawdown)) > Decimal('0.51'):
            raise ValueError('invalid_drawdown')
        drawdown_text = str(facts['rut_drawdown_rounded_pct'])
        approximate = '约'
    small_caps = (f'罗素2000指数{_move(facts["rut_change"], close=True)}，{comparison}；'
                  f'较本期已收录收盘高点回撤{approximate}{drawdown_text}%{low_clause}。')
    return {'scenario': labels[verdict],
            'slots': {'indices': indices, 'breadth': breadth, 'small_caps': small_caps,
                      'verdict': f'按本栏目区间分级，{verdict}。'},
            'bindings': {key: facts.get(key) for key in ('ndx_change', 'spx_change', 'dji_change', 'rut_change',
                       'gap', 'gap_percentile', 'gap_low', 'gap_low_date', 'rut_drawdown', 'verdict',
                       'gap_previous', 'gap_change', 'rut_low_since_date', 'rut_low_since_month',
                       'rut_peak_close', 'rut_drawdown_rounded_pct')},
            'writing_order': '首项以此前交易日纳斯达克、标普500、道琼斯收盘变化开头；随后写等权差值和罗素2000。只比较表现，不推断资金流。'}


def build_context(category: str, payload: dict) -> dict:
    """Validate the category's existing fact schema and return immutable prose slots."""
    actual = _day(payload.get('data_date'))
    facts = payload.get('facts')
    if not isinstance(facts, dict):
        raise ValueError('facts_missing')
    builder = {'token_cost': _token, 'us_barometer': _barometer, 'us_structure': _structure}.get(category)
    if builder is None:
        raise ValueError('unsupported_category')
    return builder(payload, actual, facts)
