"""Pure editorial fact spans for the five established Chinese-market briefs.

These adapters consume the validated production payload; they never fetch data,
change charts, append fixed notes, or learn facts from historical sample prose.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re


CATEGORIES = frozenset({'volume', 'turnover', 'valuation', 'liquidity', 'oil_etf'})
_NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')


def _day(value, reason='invalid_date') -> date:
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise ValueError(reason) from None


def _number(value, *, positive=False, ratio=False) -> Decimal:
    if isinstance(value, bool) or value in (None, ''):
        raise ValueError('incomplete_facts')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('invalid_number') from None
    if not result.is_finite() or positive and result <= 0 or ratio and not 0 <= result <= 100:
        raise ValueError('invalid_number')
    return result


def _prior(facts, actual: date):
    previous = _day(facts.get('previous_date'))
    if previous >= actual:
        raise ValueError('invalid_previous_date')
    return previous


def _two(value) -> str:
    return f'{value:.2f}'


def _comparison(current: Decimal, previous: Decimal) -> str:
    return '升至' if current > previous else '降至' if current < previous else '维持在'


def _normalized_numbers(text) -> set[Decimal]:
    return {Decimal(found.replace(',', '')) for found in _NUMBER.findall(str(text))}


def _check_coverage(payload, slots):
    # Keep every numeric fact already printed by the validated rule product.
    # Evergreen explanations belong to the existing delivery layer, not a plan.
    body = re.split(r'\n\s*-{3,}\s*\n', str(payload.get('dynamic_body') or ''), maxsplit=1)[0]
    notes = payload.get('fixed_notes')
    if isinstance(notes, str) and notes:
        body = body.replace(notes, '')
    source = _normalized_numbers(body)
    rendered = _normalized_numbers(''.join(slots.values()))
    if source - rendered:
        raise ValueError('unbound_rule_numbers')


def _volume(payload, actual, facts):
    previous = _prior(facts, actual)
    amount = _number(facts.get('amount_yi'), positive=True)
    ma = _number(facts.get('ma20_yi'), positive=True)
    delta = _number(facts.get('change_yi'))
    percent = _number(facts.get('change_pct'))
    if amount - delta <= 0 or delta * percent < 0 or (delta == 0) != (percent == 0):
        raise ValueError('inconsistent_volume_change')
    if abs(delta / (amount - delta) * 100 - percent) > Decimal('0.02'):
        raise ValueError('inconsistent_volume_change')
    for key, value in (('amount_wan_yi', amount / 10000), ('ma20_wan_yi', ma / 10000)):
        if key in facts and _number(facts[key]) != Decimal(_two(value)):
            raise ValueError('inconsistent_volume_units')
    streak = facts.get('streak')
    if isinstance(streak, bool) or not isinstance(streak, int) or streak < 1:
        raise ValueError('incomplete_facts')
    side = '低于' if amount < ma else '不低于'
    if facts.get('side') not in (None, side):
        raise ValueError('inconsistent_ma_side')
    scenario = 'up' if delta > 0 else 'down' if delta < 0 else 'flat'
    main = f'沪深两市成交额为{_two(amount / 10000)}万亿元，较前一有效交易日'
    if delta:
        main += f'{"增加" if delta > 0 else "减少"}{_two(abs(delta))}亿元，{ "涨幅" if percent > 0 else "降幅"}{_two(abs(percent))}%。'
    else:
        main += f'持平，变动{_two(delta)}亿元（{_two(percent)}%）。'
    comparison = f'连续{streak}个有效交易日{side}20日均线（{_two(ma / 10000)}万亿元）。'
    bindings = {'amount': {'value': str(amount), 'display': _two(amount / 10000), 'unit': '万亿元'},
                'change': {'value': str(delta), 'pct': str(percent), 'previous_date': previous.isoformat()},
                'ma20': {'value': str(ma), 'side': side, 'streak': streak}}
    return scenario, main, comparison, bindings


def _turnover(payload, actual, facts):
    _prior(facts, actual)
    if facts.get('date') != actual.isoformat() or facts.get('average_window_trading_days', 9) != 9:
        raise ValueError('inconsistent_turnover_date_or_window')
    low, high = (_number(facts.get(key), ratio=True) for key in ('low_ma9', 'high_ma9'))
    old_low, old_high = (_number(facts.get(key), ratio=True) for key in ('previous_low_ma9', 'previous_high_ma9'))
    if low + high > 100 or old_low + old_high > 100:
        raise ValueError('inconsistent_turnover_population')
    for key, delta in (('low_change_pp', low - old_low), ('high_change_pp', high - old_high)):
        if key in facts and _number(facts[key]) != delta:
            raise ValueError('inconsistent_turnover_change')
    if low == old_low and high == old_high:
        scenario = 'unchanged'
    elif low >= old_low and high <= old_high:
        scenario = 'cooling'
    elif low <= old_low and high >= old_high:
        scenario = 'warming'
    else:
        scenario = 'mixed'
    if facts.get('trend') not in (None, scenario):
        raise ValueError('inconsistent_turnover_trend')
    main = (f'A股低于3%换手率股票占比的9日均线{_comparison(low, old_low)}{_two(low)}%，'
            f'高于5%换手率股票占比的9日均线{_comparison(high, old_high)}{_two(high)}%。')
    side = '低于' if high < 17 else '高于' if high > 17 else '等于'
    comparison = f'高换手率股票占比的9日均线{side}17%参考阈值。'
    return scenario, main, comparison, {'low_ma9': {'value': str(low), 'previous': str(old_low), 'unit': '%'},
                                       'high_ma9': {'value': str(high), 'previous': str(old_high), 'unit': '%'},
                                       'threshold': {'value': '17', 'side': side}}


def _valuation(payload, actual, facts):
    latest, prior = facts.get('erp_latest'), facts.get('erp_previous')
    if not isinstance(latest, dict) or not isinstance(prior, dict):
        raise ValueError('incomplete_facts')
    if _day(latest.get('date')) != actual or _day(prior.get('date')) >= actual:
        raise ValueError('inconsistent_valuation_date')
    buffett = _number(facts.get('buffett_percent'), positive=True)
    erp, previous = (_number(row.get('erp_percent')) for row in (latest, prior))
    change = erp - previous
    scenario = 'higher' if change > 0 else 'lower' if change < 0 else 'unchanged'
    main = (f'巴菲特指数（A股总市值/上年度全年GDP）为{_two(buffett)}%；'
            f'沪深300指数股权风险溢价（ERP）为{_two(erp)}%，较{prior["date"]}')
    main += f'{"上升" if change > 0 else "下降"}{_two(abs(change))}个百分点。' if change else f'持平（变动{_two(change)}个百分点）。'
    side = '高于' if erp > 6 else '低于' if erp < 6 else '等于'
    comparison = f'当前ERP{side}6%参考线。'
    return scenario, main, comparison, {'buffett': {'value': str(buffett), 'scope': 'A股总市值/上年度全年GDP'},
                                       'erp': {'value': str(erp), 'previous': str(previous),
                                               'change_pp': str(change), 'previous_date': prior['date'], 'reference': '6'}}


def _liquidity(payload, actual, facts):
    quotes = facts.get('quotes')
    if not isinstance(quotes, dict) or quotes.get('quote_date') != actual.isoformat():
        raise ValueError('inconsistent_liquidity_date')
    try:
        stamp = datetime.strptime(quotes.get('updated_at', ''), '%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        raise ValueError('invalid_close_time') from None
    if stamp.date() != actual or (stamp.hour, stamp.minute) != (17, 0):
        raise ValueError('pending_close')
    rows = quotes.get('rows')
    if not isinstance(rows, list):
        raise ValueError('incomplete_facts')
    chosen = {}
    for key in ('DR001', 'DR007'):
        matched = [row for row in rows if isinstance(row, dict) and row.get('product') == key]
        if len(matched) != 1:
            raise ValueError('incomplete_facts')
        chosen[key] = matched[0]
        _number(chosen[key].get('weighted_rate'), positive=True)
        if chosen[key].get('updated_at') not in (None, quotes['updated_at']):
            raise ValueError('inconsistent_liquidity_date')
    row = chosen['DR007']
    rate = _number(row['weighted_rate'], positive=True)
    previous = facts.get('previous_close') or {}
    fragments, close_bindings = [], {}
    for name in ('DR001', 'DR007'):
        item = chosen[name]
        current = _number(item['weighted_rate'], positive=True)
        fragment = f'{name}收盘加权利率为{item["weighted_rate"]}%'
        if item.get('latest_rate') is not None:
            _number(item['latest_rate'], positive=True)
            fragment += f'，最新成交利率为{item["latest_rate"]}%'
        prior = previous.get(name)
        if prior is not None:
            if not isinstance(prior, dict) or _day(prior.get('date')) >= actual:
                raise ValueError('invalid_previous_date')
            old = _number(prior.get('weighted_rate'), positive=True)
            delta = (current - old) * 100
            fragment += f'；较{prior["date"]}的日加权利率'
            fragment += (f'{"上行" if delta > 0 else "下行"}{_two(abs(delta))}个基点'
                         if delta else '持平')
        fragments.append(fragment + '。')
        close_bindings[name] = {'rate': str(current), 'latest_rate': item.get('latest_rate'),
                                'previous': prior, 'unit': '%'}
    main = ''.join(fragments)
    bank, comparison = facts.get('central_bank'), ''
    scenario = 'no_policy'
    bindings = {**close_bindings, 'close_time': quotes['updated_at']}
    if bank is not None:
        if not isinstance(bank, dict) or bank.get('date') != actual.isoformat() or bank.get('term_days') != 7:
            raise ValueError('inconsistent_policy_date_or_term')
        policy = _number(bank.get('rate_percent'), positive=True)
        bid, award = (_number(bank.get(key)) for key in ('bid_yi', 'awarded_yi'))
        if min(bid, award) < 0 or award > bid:
            raise ValueError('inconsistent_policy_amount')
        spread = (rate - policy) * 100
        scenario = 'below_policy' if spread < 0 else 'above_policy' if spread > 0 else 'at_policy'
        comparison = f'较当日7天期逆回购操作利率{bank["rate_percent"]}%'
        comparison += f'{"低" if spread < 0 else "高"}{_two(abs(spread))}个基点。' if spread else '持平。'
        comparison += (f'央行当日开展{bank["awarded_yi"]}亿元7天期逆回购，'
                       f'投标量{bank["bid_yi"]}亿元，中标量{bank["awarded_yi"]}亿元。')
        bindings['policy'] = {'rate': str(policy), 'spread_bp': str(spread),
                              'bid_yi': str(bid), 'awarded_yi': str(award)}
    return scenario, main, comparison, bindings


def _oil(payload, actual, facts):
    previous = _prior(facts, actual)
    funds = facts.get('funds')
    if not isinstance(funds, list):
        raise ValueError('incomplete_facts')
    fragments, changes, bindings = [], [], {}
    for ticker, label in (('USO', 'WTI'), ('BNO', 'Brent')):
        matched = [fund for fund in funds if isinstance(fund, dict) and fund.get('ticker') == ticker]
        if len(matched) != 1:
            raise ValueError('incomplete_facts')
        fund = matched[0]
        barrels = _number(fund.get('barrels'), positive=True)
        futures = _number(fund.get('futures_barrels'))
        if futures < 0:
            raise ValueError('invalid_number')
        if ticker == 'USO' and futures > barrels:
            raise ValueError('inconsistent_oil_metric')
        if fund.get('swap_barrels_estimate') is not None:
            swap = _number(fund['swap_barrels_estimate'])
            if swap < 0 or abs(futures + swap - barrels) > Decimal('0.01'):
                raise ValueError('inconsistent_oil_metric')
        change = _number(fund['change_barrels']) if fund.get('change_barrels') is not None else None
        if change is not None and fund.get('change_basis') != 'futures_contracts_1000':
            raise ValueError('unverified_oil_change_basis')
        if change is not None and futures - change < 0:
            raise ValueError('inconsistent_oil_change')
        name = '名义敞口估算' if ticker == 'USO' else '期货持仓'
        if ticker == 'BNO' and barrels != futures:
            raise ValueError('inconsistent_oil_metric')
        text = f'{label}（{ticker}）{name}约{_two(barrels / 10000)}万桶'
        if ticker == 'USO':
            text += f'，其中期货持仓{_two(futures / 10000)}万桶'
        if change is not None:
            text += f'，期货持仓较{previous.isoformat()}'
            text += f'{"增加" if change > 0 else "减少"}{_two(abs(change) / 10000)}万桶' if change else '持平（变动0.00万桶）'
        changes.append(change)
        fragments.append(text + '。')
        bindings[ticker] = {'value_barrels': str(barrels), 'display_wan_barrels': _two(barrels / 10000),
                            'metric': name, 'change_barrels': str(change) if change is not None else None,
                            'change_basis': fund.get('change_basis'), 'previous_date': previous.isoformat()}
    scenario = ('missing_change' if any(value is None for value in changes) else
                'both_up' if all(value > 0 for value in changes) else
                'both_down' if all(value < 0 for value in changes) else
                'both_flat' if all(value == 0 for value in changes) else 'mixed')
    return scenario, fragments[0], fragments[1], bindings


def build_context(category: str, payload: dict) -> dict:
    if category not in CATEGORIES:
        raise ValueError('unsupported_category')
    actual = _day(payload.get('data_date'))
    facts = payload.get('facts')
    if not isinstance(facts, dict):
        raise ValueError('incomplete_facts')
    builder = {'volume': _volume, 'turnover': _turnover, 'valuation': _valuation,
               'liquidity': _liquidity, 'oil_etf': _oil}[category]
    scenario, main, comparison, bindings = builder(payload, actual, facts)
    printed_day = facts['quotes']['updated_at'] if category == 'liquidity' else actual.isoformat()
    slots = {'date': printed_day, 'main_fact': main, 'comparison_fact': comparison}
    _check_coverage(payload, slots)
    return {'scenario': scenario, 'slots': slots, 'bindings': bindings,
            'writing_order': {'volume': '先写成交额和日变动，再写20日均线位置。',
                              'turnover': '先对照两项九日占比均线，再交代参考阈值。',
                              'valuation': '先写巴菲特指数，再写ERP变化与参考线；不推断未来收益。',
                              'liquidity': '仅写已验证17时收盘行情及同日政策操作，不引入盘初值。',
                              'oil_etf': '依次写WTI名义敞口估算和Brent期货持仓，日变动只指期货。'}[category]}
