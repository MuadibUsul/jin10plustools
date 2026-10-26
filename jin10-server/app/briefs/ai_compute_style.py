"""Deterministic title and body for the two AI-compute fixed briefs.

The adapter stays a fact pack. This module writes the production desk voice
before the optional model call, using the 2026-10-26 sample rules. Sample
sentences are not copied with their historical numbers. Every printed number
has to already exist in the fact pack, the data date, or a threshold this
module actually used (the 1 in 低于1美元). Fixed notes stay out of the body.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

STYLE_CONTRACT = 'ai-compute-desk-20261026'
SMALL_MOVE = Decimal('3')
FLAT_MOVE = Decimal('1')
LOW_DRAWDOWN = Decimal('40')
HALVED_DRAWDOWN = Decimal('50')
NARROW_RATIO = Decimal('0.02')
MIN_RANGE_DAYS = 5
NEAR_PEAK_GAP = Decimal('5')
NEAR_LOW_GAP = Decimal('2')
BOUNCE_MOVE = Decimal('3')
_NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
_CARDS = ('A100', 'H100', 'B200')
_CN_COUNT = {2: '两日', 3: '三日', 4: '四日', 5: '五日', 6: '六日', 7: '七日', 8: '八日', 9: '九日', 10: '十日'}
_BANNED = (
    '创半年新高', '半年新高', '历史新高',
    '结构性紧缺', '全面紧缺', '烧钱', '消费降级', '开源模型',
    '供应紧缺', '需求旺盛', '需求迁移', 'Silicon Data', '定价权',
)


def apply_compute_style(payload: dict, category: str) -> dict:
    """Replace title and dynamic body when the fact pack can support the corpus voice."""
    if payload.get('style_contract') == STYLE_CONTRACT:
        return payload
    facts = payload.get('facts') or {}
    if category == 'token_cost' and not facts.get('token_price'):
        return payload
    if category == 'gpu_rental' and not all(facts.get(key) for key in ('a100', 'h100', 'b200')):
        return payload
    month_day = _month_day(payload.get('data_date') or '')
    if not month_day:
        return payload
    try:
        if category == 'token_cost':
            title, body, extra = _token_copy(facts, month_day)
        elif category == 'gpu_rental':
            title, body, extra = _gpu_copy(facts, month_day)
        else:
            return payload
        assert_supported(payload, title, body, extra)
        if not title or not body or '\n' in title:
            raise ValueError('风格稿标题或正文无效')
    except (ValueError, InvalidOperation, ArithmeticError, KeyError):
        payload.setdefault('warnings', []).append('AI算力风格模板未通过数字守卫，已保留事实稿')
        return payload
    payload['facts_copy'] = {'title': payload.get('title', ''), 'body': payload.get('dynamic_body', '')}
    payload['title'] = title
    payload['dynamic_body'] = body
    payload['style_contract'] = STYLE_CONTRACT
    return payload


def assert_supported(payload: dict, title: str, body: str, extra=()) -> None:
    allowed = allowed_numbers(payload) | {str(item) for item in extra}
    stray = present_numbers(f'{title}\n{body}') - allowed
    if stray:
        raise ValueError('风格稿出现事实包以外的数字：' + '、'.join(sorted(stray)))
    text = title + body
    for phrase in _BANNED:
        if phrase in text:
            raise ValueError('风格稿包含禁止判断：' + phrase)


def present_numbers(text: str) -> set[str]:
    scrubbed = text
    for name in _CARDS:
        scrubbed = scrubbed.replace(name, '')
    return {match.group(0).replace(',', '') for match in _NUMBER.finditer(scrubbed)}


def allowed_numbers(payload: dict) -> set[str]:
    facts = payload.get('facts') or {}
    chunks = [str(payload.get('data_date') or '')]
    for value in facts.values():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        chunks.append(str(value))
    allowed: set[str] = set()
    for chunk in chunks:
        found = present_numbers(chunk)
        allowed |= found
        allowed |= {item.lstrip('+-') for item in found}
    dates = [str(payload.get('data_date') or '')]
    for key in ('previous_date', 'prior_peak_date', 'peak_date', 'b200_peak_date',
                'range_start_date', 'range_end_date', 'prior_low_date'):
        dates.append(str(facts.get(key) or ''))
    for value in dates:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            year, month, day = value.split('-')
            allowed.update({year, month, day, str(int(month)), str(int(day))})
    return allowed


def _token_copy(facts: dict, month_day: str) -> tuple[str, str, set[str]]:
    price = str(facts['token_price'])
    price_dec = _decimal(price)
    weekly_raw = facts.get('weekly_change_pct') or ''
    weekly = _decimal(weekly_raw)
    relation = facts.get('peak_relation') or ''
    drawdown = _decimal(facts.get('drawdown_pct'))
    narrow = _token_narrow(facts, price_dec)
    sub_dollar = price_dec is not None and price_dec < 1
    low = sub_dollar and relation == 'below' and drawdown is not None and drawdown >= LOW_DRAWDOWN
    halved = low and drawdown >= HALVED_DRAWDOWN
    path = facts.get('week_path') or ''
    streak = _int(facts.get('up_streak'))
    at_low = facts.get('at_low') == 'yes'
    near_low = _near_recorded_low(facts, price_dec, at_low)
    kind = _token_kind(facts, weekly, price_dec, narrow, low, halved, path, streak, at_low, near_low)
    title = _token_title(kind, month_day, price, streak, facts)
    body = _token_body(kind, facts, month_day, price, weekly_raw, path, streak, sub_dollar and low)
    if price not in body:
        raise ValueError('风格稿遗漏Token价格')
    if kind != 'sideways' and weekly not in (None, 0) and _unsigned(weekly_raw) not in body:
        raise ValueError('风格稿遗漏Token周变化')
    extra = {'1'} if '低于1美元' in body else set()
    return title, body, extra


def _token_kind(facts, weekly, price, narrow, low, halved, path, streak, at_low, near_low) -> str:
    relation = facts.get('peak_relation') or ''
    if relation == 'tie':
        return 'tie'
    if relation == 'above':
        return 'above'
    if weekly is None:
        return 'no_week'
    change = abs(weekly)
    bounce = _bounce_pct(facts, price)
    if low and streak >= 5:
        return 'five_up'
    if low and narrow and path == 'down_then_up':
        return 'narrow_question'
    if low and weekly < 0 and bounce is not None and bounce >= BOUNCE_MOVE:
        return 'rebound_but_down'
    if low and narrow and weekly < 0 and change < SMALL_MOVE and not at_low:
        return 'sideways'
    if low and weekly > 0 and halved and change < SMALL_MOVE:
        return 'halved_rebound'
    if low and weekly > 0 and change >= SMALL_MOVE:
        return 'short_rebound'
    if low and weekly > 0:
        return 'low_rebound'
    if low and weekly < 0 and at_low and halved:
        return 'new_low_halved'
    if low and weekly < 0 and at_low:
        return 'new_low'
    if low and weekly < 0 and near_low:
        return 'near_low'
    if low and weekly < 0 and change < SMALL_MOVE:
        return 'small_down_low'
    if low and weekly < 0:
        return 'down_low'
    if change < FLAT_MOVE:
        return 'flat'
    drawdown = _decimal(facts.get('drawdown_pct'))
    if weekly > 0 and relation == 'below' and drawdown is not None and drawdown >= Decimal('20'):
        return 'rebound_unfinished'
    if weekly > 0:
        return 'up'
    if change < SMALL_MOVE and narrow:
        return 'small_down_narrow'
    if change < SMALL_MOVE:
        return 'small_down'
    if at_low:
        return 'fresh_low'
    return 'down'


def _token_title(kind, month_day, price, streak, facts) -> str:
    if kind == 'no_week':
        return f'【AI算力CPI】截至{month_day}Token支出价格报{price}美元'
    if kind == 'five_up':
        count = _CN_COUNT.get(streak) or f'{facts.get("up_streak")}日'
        return f'【AI算力CPI】Token价格连涨{count}，能否扭转下行趋势？'
    return {
        'tie': '【AI算力CPI】Token价格与已收录高点并列',
        'above': '【AI算力CPI】Token价格高于此前已收录高点',
        'narrow_question': '【AI算力CPI】Token价格波幅收窄，趋势是否扭转？',
        'rebound_but_down': '【AI算力CPI】Token价格低位反弹，但下行趋势尚未扭转',
        'sideways': '【AI算力CPI】Token价格最近一周小幅下跌，低位横盘格局延续',
        'halved_rebound': '【AI算力CPI】Token价格腰斩后暂时止跌反弹',
        'short_rebound': '【AI算力CPI】Token价格短线反弹，但下行趋势未逆转',
        'low_rebound': '【AI算力CPI】Token价格低位反弹，但下行趋势尚未扭转',
        'new_low_halved': '【AI算力CPI】Token价格跌跌不休，再次刷新年内低点',
        'new_low': '【AI算力CPI】Token价格仍未止跌，持续刷新新低',
        'near_low': '【AI算力CPI】Token价格再次接近刷新年内低点',
        'small_down_low': '【AI算力CPI】Token价格最近一周小幅下跌，仍处低位',
        'down_low': '【AI算力CPI】Token价格延续下跌，仍未摆脱下行趋势',
        'flat': '【AI算力CPI】Token价格最近七个自然日基本持平',
        'rebound_unfinished': '【AI算力CPI】Token价格持续回升，但尚未摆脱跌势',
        'up': '【AI算力CPI】Token价格一周回升',
        'small_down_narrow': '【AI算力CPI】Token价格最近一周小幅下跌，波动收窄',
        'small_down': '【AI算力CPI】Token价格最近一周小幅下跌',
        'fresh_low': '【AI算力CPI】Token价格延续跌势，刷新阶段新低',
        'down': '【AI算力CPI】Token价格延续下跌',
    }[kind]


def _token_body(kind, facts, month_day, price, weekly_raw, path, streak, show_below) -> str:
    move = _week_move(weekly_raw)
    trend = _trend_clause(facts) if show_below or kind in ('rebound_unfinished',) else ''
    drop = _drop_clause(facts)
    if kind == 'sideways':
        return _sideways_body(facts, month_day, price, path, move)
    if kind == 'narrow_question':
        return _question_body(month_day, price, path, move, show_below, trend, drop)
    if kind == 'five_up':
        count = _CN_COUNT.get(streak) or f'{facts.get("up_streak")}日'
        second = f'该指标连升{count}' + (f'，{move}' if move else '')
        third = _below_trend(show_below, trend, lead='但')
        return _sentences([
            f'截至{month_day}，Token支出价格报{price}美元',
            second, third, _cost_clause(drop),
        ])
    if kind in ('rebound_but_down', 'low_rebound', 'halved_rebound'):
        return _sentences([
            f'Token支出价格指数最新报{price}' + (f'，{move}' if move else ''),
            _below_trend(show_below, trend, lead='但'),
            _cost_clause(drop),
        ])
    if kind == 'short_rebound':
        contrast = '虽然短线反弹，但' + _below_trend(show_below, trend, lead='')
        return _sentences([
            f'Token支出价格指数最新报{price}' + (f'，{move}' if move else ''),
            contrast, _cost_clause(drop),
        ])
    if kind in ('new_low', 'new_low_halved'):
        month_end = _month_end(facts.get('prior_peak_date') or '')
        follow = f'仍在刷新阶段低点，延续{month_end}以来下行趋势' if month_end else '仍在刷新阶段低点'
        return _sentences([
            f'Token支出价格指数报{price}' + (f'，{move}' if move else '') + f'，{follow}',
            _cost_clause(drop),
        ])
    if kind == 'near_low':
        return _sentences([
            f'Token支出价格指数报{price}' + (f'，{move}' if move else '') + '，再次逼近已收录低点',
            _below_trend(show_below, trend, lead=''),
            _cost_clause(drop),
        ])
    if kind in ('small_down_low', 'down_low', 'rebound_unfinished'):
        return _sentences([
            f'截至{month_day}，Token支出价格报{price}美元' + (f'，{move}' if move else ''),
            _below_trend(show_below, trend, lead=''),
            _cost_clause(drop) if show_below else drop,
        ])
    relation = _relation_clause(facts)
    ranged = _range_clause(facts)
    if kind == 'no_week':
        return _sentences([f'截至{month_day}，Token支出价格报{price}美元，七个自然日前的基准值缺失，本次不报告周变化', relation])
    if kind == 'small_down_narrow':
        return _sentences([
            f'截至{month_day}，Token支出价格报{price}美元' + (f'，{move}' if move else ''),
            '短期价格波动明显收窄', ranged, relation,
        ])
    if kind == 'fresh_low':
        return _sentences([
            f'截至{month_day}，Token支出价格报{price}美元' + (f'，{move}' if move else '') + '，并低于此前已收录价格',
            relation,
        ])
    return _sentences([
        f'截至{month_day}，Token支出价格报{price}美元' + (f'，{move}' if move else ''),
        ranged, relation or drop,
    ])


def _sideways_body(facts, month_day, price, path, move) -> str:
    low, high = facts.get('range_low') or '', facts.get('range_high') or ''
    span = _span(facts.get('range_start_date') or '', facts.get('range_end_date') or '')
    path_word = {'down_then_flat': '先跌后稳', 'flat': '窄幅波动', 'down': '延续下行'}.get(path, '波动收窄')
    if low and high and span and path == 'down_then_flat':
        return (
            f'最近一周，Token支出价格指数保持在{low}美元上方。'
            f'指数先跌后稳，{span}基本维持在{low}—{high}美元区间，短期价格波动明显收窄。'
            '不过尽管价格下行速度有所放缓，但尚未出现持续反弹迹象。'
        )
    if low and high and span:
        return (
            f'最近一周，Token支出价格指数保持在{low}美元上方。'
            f'指数{path_word}，{span}基本维持在{low}—{high}美元区间，短期价格波动明显收窄。'
            '不过尽管价格下行速度有所放缓，但尚未出现持续反弹迹象。'
        )
    if low and high:
        return (
            f'最近一周，Token支出价格指数保持在{low}美元上方。'
            f'指数{path_word}，基本维持在{low}—{high}美元区间，短期价格波动明显收窄。'
            '不过尽管价格下行速度有所放缓，但尚未出现持续反弹迹象。'
        )
    return _sentences([
        f'截至{month_day}，Token支出价格报{price}美元' + (f'，{move}' if move else ''),
        '短期价格波动明显收窄，尚未出现持续反弹迹象',
    ])


def _question_body(month_day, price, path, move, show_below, trend, drop) -> str:
    path_word = {'down_then_up': '先跌后升', 'down_then_flat': '先跌后稳', 'up_then_down': '先升后跌'}.get(path, '')
    indicator = []
    if path_word and move:
        indicator.append(f'该指标{path_word}，{move}')
    elif path_word:
        indicator.append(f'该指标{path_word}')
    elif move:
        indicator.append(f'该指标{move}')
    if show_below:
        indicator.append('目前仍低于1美元')
    if trend:
        indicator.append(trend)
    week = '最近一周，' + '，'.join(indicator) + '，但价格波幅有所收窄' if indicator else '最近一周价格波幅有所收窄'
    return _sentences([
        f'截至{month_day}，Token支出价格维持在{price}美元左右',
        week, _cost_clause(drop),
    ])


def _gpu_copy(facts: dict, month_day: str) -> tuple[str, str, set[str]]:
    signs = _signs(facts)
    relation = facts.get('b200_peak_relation') or ''
    gap = _decimal(facts.get('b200_gap_pct'))
    near = relation == 'below' and gap is not None and gap <= NEAR_PEAK_GAP
    kind = _gpu_kind(signs, relation, near)
    title = _gpu_title(kind, month_day)
    body = _gpu_body(kind, facts, month_day, signs, relation, near)
    for key in ('a100', 'h100', 'b200'):
        if str(facts[key]) not in body:
            raise ValueError(f'风格稿遗漏{key}价格')
        raw = facts.get(f'{key}_change_pct') or ''
        value = _decimal(raw)
        if value not in (None, 0) and _unsigned(raw) not in body:
            raise ValueError(f'风格稿遗漏{key}周变化')
    return title, body, set()


def _gpu_kind(signs, relation, near) -> str:
    if relation == 'tie':
        return 'tie'
    b200 = signs['b200'] if signs else None
    h100 = signs['h100'] if signs else None
    a100 = signs['a100'] if signs else None
    if relation == 'above' and a100 is not None and a100 <= 0:
        return 'new_high_diverge'
    if relation == 'above':
        return 'above'
    if signs and b200 > 0 and a100 < 0 and near:
        return 'diverge_up'
    if signs and b200 > 0 and a100 < 0 and h100 <= 0 and abs(b200) < SMALL_MOVE:
        return 'small_rebound'
    if signs and b200 > 0 and a100 < 0 and h100 >= SMALL_MOVE:
        return 'resilient'
    if signs and b200 > 0 and a100 < 0:
        return 'diverge_up'
    if signs and near and b200 > 0 and abs(a100) < 1 and abs(h100) < 1:
        return 'high_chop'
    if signs and near and b200 < 0:
        return 'high_chop'
    if signs and all(value < 0 for value in signs.values()):
        return 'pullback'
    if signs and all(value > 0 for value in signs.values()):
        return 'all_up'
    if signs and b200 > 0 and h100 <= 0 and a100 <= 0:
        return 'leaders_idle'
    if signs and all(value == 0 for value in signs.values()):
        return 'flat'
    if not signs:
        return 'no_week'
    return 'mixed'


def _gpu_title(kind, month_day) -> str:
    return {
        'tie': '【AI景气度指标】B200租赁价与已收录高点并列',
        'new_high_diverge': '【AI景气度指标】高端GPU租赁价新高，市场代际分化加剧',
        'above': '【AI景气度指标】高端GPU租赁价高于此前高点',
        'diverge_up': '【AI景气度指标】高端GPU租赁价走强，市场呈现代际分化',
        'small_rebound': '【AI景气度指标】B200小幅回升，H100/A100低位震荡',
        'resilient': '【AI景气度指标】高端GPU需求保持韧性，老版芯片价格继续下探',
        'high_chop': '【AI景气度指标】高端GPU租赁价高位波动，代际价格分化持续',
        'pullback': '【AI景气度指标】GPU租赁价格有所回落',
        'all_up': '【AI景气度指标】三卡租赁价格一周回升',
        'leaders_idle': '【AI景气度指标】B200一周回升，前两代租赁价未跟随',
        'flat': '【AI景气度指标】三卡租赁价格七个自然日持平',
        'no_week': f'【AI景气度指标】截至{month_day}三卡租赁价格',
        'mixed': '【AI景气度指标】三卡租赁价格涨跌不一',
    }[kind]


def _gpu_body(kind, facts, month_day, signs, relation, near) -> str:
    b200_raw = facts.get('b200_change_pct') or ''
    b200_move = _desk_move(b200_raw)
    b200_value = signs['b200'] if signs else _decimal(b200_raw)
    verb = '升至' if b200_value is not None and b200_value > 0 else '回落至' if b200_value is not None and b200_value < 0 else '报'
    if b200_move is None:
        lead = f"截至{month_day}，英伟达B200租赁价格报{facts['b200']}美元/GPU每小时，周变化基准缺失"
    elif b200_move == '与上周基本持平':
        lead = f"截至{month_day}，英伟达B200租赁价格{verb}{facts['b200']}美元/GPU每小时，{b200_move}"
    else:
        lead = f"截至{month_day}，英伟达B200租赁价格{verb}{facts['b200']}美元/GPU每小时，较一周前{b200_move}"
    peak = _gpu_peak_phrase(facts, relation, near)
    if peak:
        lead += f'，{peak}'
    sync = f"同期，{_card_bit('H100', facts['h100'], facts.get('h100_change_pct'), True)}；{_card_bit('A100', facts['a100'], facts.get('a100_change_pct'), False)}。"
    return lead + '。' + sync + _gpu_div(kind)


def _gpu_div(kind) -> str:
    if kind in ('diverge_up', 'new_high_diverge', 'resilient'):
        return '数据显示，新一代GPU租赁价格持续走强，而上一代产品表现相对疲弱，反映AI算力租赁市场出现代际价格分化。'
    if kind in ('high_chop', 'small_rebound', 'leaders_idle'):
        return '数据显示，新一代GPU租赁价格维持高位，而上一代产品表现相对疲弱，反映AI算力租赁市场的代际价格分化。'
    return ''


def _gpu_peak_phrase(facts, relation, near) -> str:
    peak = facts.get('b200_peak') or ''
    if not peak:
        return ''
    if relation == 'below' and near:
        return f'仍接近此前高点{peak}美元'
    if relation == 'below' and facts.get('b200_gap_pct'):
        return f"较此前高点{peak}美元低{facts['b200_gap_pct']}%"
    if relation == 'tie':
        return f'与此前高点{peak}美元并列'
    if relation == 'above':
        return f'高于此前高点{peak}美元'
    return ''


def _card_bit(label, price, raw, lease) -> str:
    name = f'{label}租赁价格报{price}美元' if lease else f'{label}报{price}美元'
    move = _desk_move(raw)
    if move is None:
        return f'{name}，周变化基准缺失'
    return f'{name}，{move}'


def _desk_move(raw) -> str | None:
    value = _decimal(raw)
    if value is None:
        return None
    if value == 0:
        return '与上周基本持平'
    if 0 < value < 1:
        word = '微涨'
    elif value > 0:
        word = '上涨'
    else:
        word = '下跌'
    return f'{word}{_unsigned(raw)}%'


def _week_move(raw) -> str:
    move = _desk_move(raw)
    if move is None:
        return ''
    if move == '与上周基本持平':
        return '较一周前持平'
    return f'较一周前{move}'


def _below_trend(show_below, trend, lead) -> str:
    parts = []
    if show_below:
        parts.append(('但目前' if lead == '但' else '目前') + '仍低于1美元')
    if trend:
        parts.append(trend)
    if not parts:
        return ''
    text = '，'.join(parts)
    if lead == '但' and not text.startswith('但'):
        return '但' + text
    return text


def _trend_clause(facts) -> str:
    month_end = _month_end(facts.get('prior_peak_date') or '')
    if not month_end:
        return ''
    return f'整体仍未摆脱{month_end}以来的下行趋势中'


def _drop_clause(facts) -> str:
    if facts.get('peak_relation') != 'below':
        return ''
    prior = facts.get('prior_peak_price') or ''
    when = _month_day(facts.get('prior_peak_date') or '')
    drawdown = facts.get('drawdown_pct') or ''
    if not prior or not when or not drawdown:
        return ''
    return f'目前较{when}高点{prior}下跌{drawdown}%'


def _cost_clause(drop) -> str:
    if drop:
        return drop + '，反映AI模型单位Token支出成本持续下降'
    return '反映AI模型单位Token支出成本持续下降'


def _relation_clause(facts) -> str:
    prior = facts.get('prior_peak_price') or ''
    when = _month_day(facts.get('prior_peak_date') or '')
    relation = facts.get('peak_relation') or ''
    if not prior or not when:
        return ''
    if relation == 'tie':
        return f'该价格与此前已收录高点{prior}美元（{when}）持平'
    if relation == 'above':
        return f'该价格高于此前已收录高点{prior}美元（{when}）'
    return _drop_clause(facts)


def _range_clause(facts) -> str:
    low, high = facts.get('range_low') or '', facts.get('range_high') or ''
    if not low or not high:
        return ''
    return f'最近七日已收录价格区间为{low}—{high}美元'


def _token_narrow(facts: dict, price: Decimal | None) -> bool:
    if price in (None, 0):
        return False
    count = _int(facts.get('range_count'))
    low, high = _decimal(facts.get('range_low')), _decimal(facts.get('range_high'))
    if count < MIN_RANGE_DAYS or low is None or high is None or high < low:
        return False
    return (high - low) / price <= NARROW_RATIO


def _bounce_pct(facts, price):
    low = _decimal(facts.get('range_low'))
    if price is None or low in (None, 0) or price <= low:
        return None
    return (price - low) / low * Decimal('100')


def _near_recorded_low(facts, price, at_low) -> bool:
    if at_low or price is None:
        return False
    prior = _decimal(facts.get('prior_low_price'))
    if prior in (None, 0) or price <= prior:
        return False
    return (price - prior) / prior * Decimal('100') <= NEAR_LOW_GAP


def _signs(facts: dict):
    signs = {}
    for key in ('b200', 'h100', 'a100'):
        value = _decimal(facts.get(f'{key}_change_pct'))
        if value is None:
            return None
        signs[key] = value
    return signs


def _sentences(parts: list[str]) -> str:
    cleaned = [(part or '').strip().strip('。') for part in parts]
    cleaned = [part for part in cleaned if part]
    return '。'.join(cleaned) + '。'


def _span(start: str, end: str) -> str:
    start_month, start_day = _month_day_parts(start)
    end_month, end_day = _month_day_parts(end)
    if not start_month or not end_month:
        return ''
    if start_month == end_month:
        return f'{start_month}月{start_day}日至{end_day}日'
    return f'{start_month}月{start_day}日至{end_month}月{end_day}日'


def _month_end(value: str) -> str:
    month, _day = _month_day_parts(value)
    return f'{month}月末' if month else ''


def _month_day_parts(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return '', ''
    _year, month, day = value.split('-')
    return str(int(month)), str(int(day))


def _month_day(value: str) -> str:
    month, day = _month_day_parts(value)
    if not month:
        return ''
    return f'{month}月{day}日'


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _unsigned(raw) -> str:
    return str(raw).strip().lstrip('+-')


def _direction(value: Decimal) -> str:
    if value > 0:
        return '上涨'
    if value < 0:
        return '下跌'
    return '持平'


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None
