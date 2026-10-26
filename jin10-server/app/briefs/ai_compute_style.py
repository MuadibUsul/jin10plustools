"""Deterministic title and body for the two AI-compute fixed briefs.

The adapter stays a fact pack. This module is the production wording: it runs in
the Skill stage before the optional model call, so a model outage still lands in
the same voice as the style corpus. Sample prose is never copied from history.md.
Every printed number has to already exist in the fact pack or in the data date.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

STYLE_CONTRACT = 'ai-compute-editorial-20261026-v2'
SMALL_MOVE = Decimal('3')
FLAT_MOVE = Decimal('1')
LOW_DRAWDOWN = Decimal('40')
HALVED_DRAWDOWN = Decimal('50')
NEAR_PEAK_GAP = Decimal('5')
_NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')
_CARDS = ('A100', 'H100', 'B200')
_BANNED = (
    '创半年新高', '半年新高', '历史新高', '创新高', '新高', '新低',
    '结构性紧缺', '全面紧缺', '定价权', '烧钱', '消费降级', '需求迁移',
    '供应紧缺', '需求旺盛', '开源模型', '止跌', '紧缺',
)
_CORPUS = {
    'token_cost': (18, '【AI算力CPI】Token价格最近一周小幅下跌，低位横盘格局延续'),
    'gpu_rental': (11, '【AI景气度指标】高端GPU租赁价创半年新高，市场呈现代际分化'),
}


def require_corpus(category: str) -> None:
    """Keep the executable templates tied to the two distinct approved corpora."""
    from .skill_ai import _history

    count, anchor = _CORPUS[category]
    text = _history(category)
    if text.count('### 风格样本 ') != count or anchor not in text:
        raise ValueError(f'{category} 风格语料不完整')


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
        require_corpus(category)
        if category == 'token_cost':
            title, body = _token_copy(facts, month_day)
        elif category == 'gpu_rental':
            title, body = _gpu_copy(facts, month_day)
        else:
            return payload
        assert_supported(payload, title, body)
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


def assert_supported(payload: dict, title: str, body: str) -> None:
    extra = present_numbers(f'{title}\n{body}') - allowed_numbers(payload)
    if extra:
        raise ValueError('风格稿出现事实包以外的数字：' + '、'.join(sorted(extra)))
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
    for key in ('previous_date', 'prior_peak_date', 'peak_date', 'b200_peak_date'):
        dates.append(str(facts.get(key) or ''))
    for value in dates:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            year, month, day = value.split('-')
            allowed.update({year, month, day, str(int(month)), str(int(day))})
    return allowed


def _token_copy(facts: dict, month_day: str) -> tuple[str, str]:
    price = str(facts['token_price'])
    weekly_raw = facts.get('weekly_change_pct') or ''
    weekly = _decimal(weekly_raw)
    relation = facts.get('peak_relation') or ''
    drawdown = _decimal(facts.get('drawdown_pct'))
    narrow = facts.get('tail_narrowed') is True
    low = relation == 'below' and drawdown is not None and drawdown >= LOW_DRAWDOWN
    halved = relation == 'below' and drawdown is not None and drawdown >= HALVED_DRAWDOWN
    title = _token_title(month_day, price, weekly, relation, low, halved, narrow)
    first = f'截至{month_day}，Token支出价格指数报{price}美元/百万Token'
    move = _move_text(weekly_raw)
    if move is not None:
        previous = _month_day(facts.get('previous_date') or '')
        first += f'，较{previous if previous else "七个自然日前"}{move}'
    else:
        first += '，七个自然日前的基准价缺失，暂不计算周变化'
    sentences = [first + '。']
    if narrow and facts.get('tail_start') and facts.get('tail_low') and facts.get('tail_high'):
        period = _date_span(facts['tail_start'], month_day)
        sentences.append(f'{period}，指数介于{facts["tail_low"]}—{facts["tail_high"]}美元/百万Token，短期波动较此前五日收窄。')
    elif int(facts.get('range_count') or 0) >= 5 and facts.get('range_low') and facts.get('range_high'):
        sentences.append(f'最近七日，已收录价格介于{facts["range_low"]}—{facts["range_high"]}美元/百万Token。')
    peak = _token_peak_clause(facts, relation)
    if peak:
        sentences.append(peak + '。')
    body = ''.join(sentences)
    if price not in body:
        raise ValueError('风格稿遗漏Token价格')
    if weekly is not None and weekly != 0 and _unsigned(weekly_raw) not in body:
        raise ValueError('风格稿遗漏Token周变化')
    return title, body


def _token_title(month_day, price, weekly, relation, low, halved, narrow) -> str:
    if relation == 'tie':
        return '【AI算力CPI】Token价格与已收录高点并列'
    if relation == 'above':
        return '【AI算力CPI】Token价格高于此前已收录高点'
    if weekly is None:
        return f'【AI算力CPI】截至{month_day}Token支出价格报{price}美元/百万Token'
    change = abs(weekly)
    if change < FLAT_MOVE:
        if low and narrow:
            return '【AI算力CPI】Token价格低位横盘，波动收窄'
        if narrow:
            return '【AI算力CPI】Token价格波动收窄'
        if low:
            return '【AI算力CPI】Token价格低位横盘'
        return '【AI算力CPI】Token价格最近七个自然日基本持平'
    if weekly > 0 and relation == 'below':
        if halved:
            return '【AI算力CPI】Token价格腰斩后一周回升，趋势是否扭转？'
        if low:
            return '【AI算力CPI】Token价格低位回升，趋势是否扭转？'
        return '【AI算力CPI】Token价格一周回升，趋势是否扭转？'
    if weekly > 0:
        return '【AI算力CPI】Token价格一周回升'
    if change < SMALL_MOVE and narrow and low:
        return '【AI算力CPI】Token价格最近一周小幅下跌，低位横盘格局延续'
    if change < SMALL_MOVE and narrow:
        return '【AI算力CPI】Token价格最近一周小幅下跌，波动收窄'
    if change < SMALL_MOVE and low:
        return '【AI算力CPI】Token价格最近一周小幅下跌，仍处低位'
    if change < SMALL_MOVE:
        return '【AI算力CPI】Token价格最近一周小幅下跌'
    if low:
        return '【AI算力CPI】Token价格延续下跌，仍处低位'
    return '【AI算力CPI】Token价格延续下跌'


def _token_peak_clause(facts: dict, relation: str) -> str:
    prior = facts.get('prior_peak_price') or ''
    prior_day = _month_day(facts.get('prior_peak_date') or '')
    drawdown = facts.get('drawdown_pct') or ''
    if not prior or not prior_day:
        return ''
    if relation == 'below' and drawdown:
        return f'较{prior_day}的已收录高点{prior}美元/百万Token，当前累计回落{drawdown}%'
    if relation == 'tie':
        return f'该价格与此前已收录高点{prior}美元/百万Token（{prior_day}）持平，属于并列高位'
    if relation == 'above':
        return f'该价格高于此前已收录高点{prior}美元/百万Token（{prior_day}）'
    return ''


def _gpu_copy(facts: dict, month_day: str) -> tuple[str, str]:
    signs = _signs(facts)
    relation = facts.get('b200_peak_relation') or ''
    title = _gpu_title(facts, month_day, signs, relation)
    previous = _month_day(facts.get('previous_date') or '')
    b200_move = _move_text(facts.get('b200_change_pct'))
    verb = '升至' if signs and signs['b200'] > 0 else '回落至' if signs and signs['b200'] < 0 else '报'
    first = f'截至{month_day}，英伟达B200租赁价格{verb}{facts["b200"]}美元/GPU每小时'
    if b200_move is not None:
        first += f'，较{previous if previous else "七个自然日前"}{b200_move}'
    else:
        first += '，七个自然日前的基准价缺失'
    peak = _gpu_peak_clause(facts, relation)
    if peak:
        first += '，' + peak
    second = (f'同期，{_card("H100", facts["h100"], facts.get("h100_change_pct"))}；'
              f'{_card("A100", facts["a100"], facts.get("a100_change_pct"))}')
    sentences = [first + '。', second + '。']
    divergence = _divergence(signs)
    if divergence:
        sentences.append(divergence + '。')
    body = ''.join(sentences)
    for key in ('a100', 'h100', 'b200'):
        if str(facts[key]) not in body:
            raise ValueError(f'风格稿遗漏{key}价格')
        raw = facts.get(f'{key}_change_pct') or ''
        value = _decimal(raw)
        if value not in (None, 0) and _unsigned(raw) not in body:
            raise ValueError(f'风格稿遗漏{key}周变化')
    return title, body


def _gpu_title(facts, month_day, signs, relation) -> str:
    gap = _decimal(facts.get('b200_gap_pct'))
    near = relation == 'below' and gap is not None and gap <= NEAR_PEAK_GAP
    ktype = bool(signs) and signs['b200'] > 0 and signs['a100'] < 0 and signs['h100'] <= 0
    diverge = bool(signs) and ((signs['b200'] > 0 > signs['a100']) or (signs['a100'] > 0 > signs['b200']))
    older_idle = bool(signs) and signs['b200'] > 0 and signs['h100'] <= 0 and signs['a100'] <= 0
    if near and diverge and signs['b200'] > 0:
        return '【AI景气度指标】高端GPU租赁价接近已收录高点，代际价格分化'
    if relation == 'tie' and ktype:
        return '【AI景气度指标】B200与已收录高点并列，三卡价格呈K型分化'
    if relation == 'tie' and diverge:
        return '【AI景气度指标】B200与已收录高点并列，三卡价格分化'
    if relation == 'tie':
        return '【AI景气度指标】B200租赁价与已收录高点并列'
    if relation == 'above' and ktype:
        return '【AI景气度指标】B200高于此前已收录高点，与前两代呈K型分化'
    if relation == 'above' and diverge:
        return '【AI景气度指标】B200高于此前已收录高点，A100走势相反'
    if relation == 'above':
        return '【AI景气度指标】B200租赁价高于此前已收录高点'
    if ktype:
        return '【AI景气度指标】高端GPU租赁价呈K型分化，B200强于前两代'
    if diverge and signs['b200'] > 0:
        return '【AI景气度指标】高端GPU租赁价分化，B200走强、A100走弱'
    if diverge:
        return '【AI景气度指标】高端GPU租赁价分化，B200走弱、A100走强'
    if older_idle:
        return '【AI景气度指标】B200一周回升，前两代租赁价未跟随'
    if signs and signs['b200'] < 0 and near:
        return '【AI景气度指标】高端GPU租赁价回落，仍接近已收录高点'
    if signs and signs['b200'] > 0 and near:
        return '【AI景气度指标】高端GPU租赁价一周回升，仍接近已收录高点'
    if signs and all(value > 0 for value in signs.values()):
        return '【AI景气度指标】三卡租赁价格一周回升'
    if signs and all(value < 0 for value in signs.values()):
        return '【AI景气度指标】三卡租赁价格一周回落'
    if signs and all(value == 0 for value in signs.values()):
        return '【AI景气度指标】三卡租赁价格七个自然日持平'
    if signs:
        return '【AI景气度指标】三卡租赁价格涨跌不一'
    return f'【AI景气度指标】截至{month_day}三卡租赁价格'


def _divergence(signs) -> str:
    if not signs:
        return ''
    if signs['b200'] > 0 and signs['a100'] < 0 and signs['h100'] <= 0:
        return 'B200一周上涨，H100与A100未跟随上涨，三卡价格呈K型分化'
    if signs['b200'] > 0 > signs['a100']:
        return '新一代B200一周走强，而A100下跌，三卡租赁价格呈现代际分化'
    if signs['a100'] > 0 > signs['b200']:
        return 'B200一周下跌、A100一周上涨，三卡价格呈现代际分化'
    if signs['b200'] > 0 and signs['h100'] <= 0 and signs['a100'] <= 0:
        return 'B200一周上涨，H100与A100未跟随上涨'
    return ''


def _gpu_peak_clause(facts: dict, relation: str) -> str:
    peak = facts.get('b200_peak') or ''
    peak_day = _month_day(facts.get('b200_peak_date') or '')
    gap = facts.get('b200_gap_pct') or ''
    if not peak:
        return ''
    located = f'（{peak_day}）' if peak_day else ''
    if relation == 'below' and gap:
        return f'距离此前已收录高点{peak}美元/GPU每小时{located}仍低{gap}%'
    if relation == 'tie':
        return f'B200与此前已收录高点{peak}美元/GPU每小时{located}持平，属于并列高位'
    if relation == 'above':
        return f'B200高于此前已收录高点{peak}美元/GPU每小时{located}'
    return ''


def _card(label: str, price, change_raw) -> str:
    name = f'{label}租赁价格报{price}美元' if label == 'H100' else f'{label}报{price}美元'
    move = _move_text(change_raw)
    if move is None:
        return f'{name}，周变化基准缺失'
    value = _decimal(change_raw)
    if value is not None and 0 < value < 1:
        move = f'微涨{_unsigned(change_raw)}%'
    return f'{name}，{move}'


def _signs(facts: dict):
    signs = {}
    for key in ('b200', 'h100', 'a100'):
        value = _decimal(facts.get(f'{key}_change_pct'))
        if value is None:
            return None
        signs[key] = value
    return signs


def _move_text(raw) -> str | None:
    value = _decimal(raw)
    if value is None:
        return None
    if value == 0:
        return '持平'
    return f'{_direction(value)}{_unsigned(raw)}%'


def _direction(value: Decimal) -> str:
    if value > 0:
        return '上涨'
    if value < 0:
        return '下跌'
    return '持平'


def _unsigned(raw) -> str:
    return str(raw).strip().lstrip('+-')


def _decimal(value):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _month_day(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return ''
    year, month, day = value.split('-')
    try:
        return f'{int(month)}月{int(day)}日'
    except ValueError:
        return ''


def _date_span(start: str, end: str) -> str:
    beginning = _month_day(start)
    if not beginning:
        return end
    month = beginning.split('月', 1)[0] + '月'
    return f'{beginning}至{end.removeprefix(month)}' if end.startswith(month) else f'{beginning}至{end}'
