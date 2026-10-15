"""Weekday publication policy. Deliberately no holiday calendar."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CHINA = ZoneInfo('Asia/Shanghai')
CATEGORIES = {
    'volume': '沪深两市成交额', 'turnover': 'A股换手率',
    'valuation': '市场估值指标', 'liquidity': '流动性指标', 'oil_etf': '原油ETF持仓',
}


def previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def expected_date(category: str, issue_day: date) -> date:
    if category not in CATEGORIES:
        raise ValueError('Unknown category')
    return issue_day if category == 'liquidity' else previous_weekday(issue_day)


def production_window(now: datetime) -> bool:
    local = now.astimezone(CHINA)
    return local.weekday() < 5 and time(8) <= local.time().replace(tzinfo=None) < time(10)


def first_run(category: str) -> time:
    return time(9, 20) if category == 'liquidity' else time(8)


def next_slot(category: str, now: datetime) -> datetime | None:
    local = now.astimezone(CHINA)
    slots = (time(9, 20), time(9, 25), time(9, 40), time(9, 55)) if category == 'liquidity' else (time(8), time(8, 30), time(9), time(9, 20), time(9, 40), time(9, 55))
    return next((datetime.combine(local.date(), slot, CHINA) for slot in slots
                 if datetime.combine(local.date(), slot, CHINA) > local), None)
