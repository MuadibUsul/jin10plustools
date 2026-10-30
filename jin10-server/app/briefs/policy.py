"""Market-specific data dates and a daily morning production window."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CHINA = ZoneInfo('Asia/Shanghai')
CATEGORIES = {
    'volume': '沪深两市成交额', 'turnover': 'A股换手率',
    'valuation': '市场估值指标', 'liquidity': '流动性指标', 'oil_etf': '原油ETF持仓',
    'us_barometer': '美股晴雨表',
    'us_structure': '美股市场结构',
    'token_cost': 'AI算力CPI',
    'gpu_rental': 'AI景气度指标',
}
AUTO_CATEGORIES = ('liquidity', 'volume', 'turnover', 'valuation', 'oil_etf',
                   'us_barometer', 'us_structure')


def previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def expected_date(category: str, issue_day: date) -> date:
    if category not in CATEGORIES:
        raise ValueError('Unknown category')
    from .trading_calendar import market_for, previous_session
    return previous_session(market_for(category),issue_day)


def production_window(now: datetime) -> bool:
    local = now.astimezone(CHINA)
    return time(8) <= local.time().replace(tzinfo=None) < time(10)


def first_run(category: str) -> time:
    return time(8)


def next_slot(category: str, now: datetime) -> datetime | None:
    local = now.astimezone(CHINA)
    slots = (time(8), time(8, 30), time(9), time(9, 10), time(9, 20), time(9, 40), time(9, 55)) if category == 'liquidity' else (time(8), time(8, 30), time(9), time(9, 20), time(9, 40), time(9, 55))
    return next((datetime.combine(local.date(), slot, CHINA) for slot in slots
                 if datetime.combine(local.date(), slot, CHINA) > local), None)
