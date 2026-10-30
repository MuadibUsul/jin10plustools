"""Market-specific sessions with explicit verified horizons; never guess a year."""
from datetime import date, timedelta
from functools import lru_cache
import json
from pathlib import Path


class CalendarUnavailable(ValueError):
    pass


@lru_cache(maxsize=1)
def calendars():
    return json.loads((Path(__file__).parent / 'calendars/sessions.json').read_text(encoding='utf-8'))


def market_for(category):
    if category in ('volume', 'turnover', 'valuation'):
        return 'xshg'
    if category == 'liquidity':
        return 'cfets'
    if category in ('oil_etf', 'us_barometer', 'us_structure'):
        return 'xnys'
    if category in ('token_cost', 'gpu_rental'):
        return 'daily'
    raise ValueError('Unknown category')


def _nth(year, month, weekday, occurrence):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday-first.weekday()) % 7 + (occurrence-1)*7)


def _observed(day):
    return day + timedelta(days=-1 if day.weekday() == 5 else 1 if day.weekday() == 6 else 0)


def _easter(year):
    a=year%19; b=year//100; c=year%100; d=b//4; e=b%4
    f=(b+8)//25; g=(b-f+1)//3; h=(19*a+b-d-g+15)%30
    i=c//4; k=c%4; l=(32+2*e+2*i-h-k)%7; m=(a+11*h+22*l)//451
    value=h+l-7*m+114
    return date(year,value//31,value%31+1)


def _us_holidays(year):
    # Saturday New Year's Day has no Friday observance at NYSE.
    new_year=date(year,1,1)
    days={new_year + timedelta(days=1 if new_year.weekday()==6 else 0),
          _nth(year,1,0,3), _nth(year,2,0,3), _easter(year)-timedelta(days=2),
          _nth(year,9,0,1), _nth(year,11,3,4), _observed(date(year,7,4)),
          _observed(date(year,12,25))}
    last=date(year,5,31)
    days.add(last-timedelta(days=last.weekday()))
    if year>=2022: days.add(_observed(date(year,6,19)))
    return days


def is_session(market, day):
    day=date.fromisoformat(day) if isinstance(day,str) else day
    if market=='daily': return True
    spec=calendars()[market]
    if not spec['start'] <= day.isoformat() <= spec['end']:
        raise CalendarUnavailable(f'{market}未核验{day.year}年交易日历；停止推测目标日期')
    if market=='xshg': return day.weekday()<5 and day.isoformat() not in spec['closed']
    if market=='xnys':
        return day.weekday()<5 and day not in _us_holidays(day.year) and day.isoformat() not in spec['adhoc_closed']
    return (day.isoformat() in spec['open_weekends'] or day.weekday()<5) and not any(
        start<=day.isoformat()<=end for start,end in spec['closed_ranges'])


def previous_session(market, day):
    day=date.fromisoformat(day) if isinstance(day,str) else day
    for _ in range(32):
        day-=timedelta(days=1)
        if is_session(market,day): return day
    raise CalendarUnavailable('交易日历中没有可用的前一交易日')


def sessions(market, start, end):
    start=date.fromisoformat(start) if isinstance(start,str) else start
    end=date.fromisoformat(end) if isinstance(end,str) else end
    result=[]
    while start<=end:
        if is_session(market,start): result.append(start.isoformat())
        start+=timedelta(days=1)
    return result


def require_window(rows, market, end, count, *, label='数据'):
    """Require actual consecutive sessions, not merely enough observed rows."""
    cursor=date.fromisoformat(end)
    if not is_session(market,cursor): raise ValueError(f'{label}目标日不是交易日：{end}')
    wanted=[]
    for _ in range(count):
        wanted.append(cursor.isoformat())
        cursor=previous_session(market,cursor)
    available={row['date'] for row in rows}
    missing=sorted(set(wanted)-available)
    if missing: raise ValueError(f'{label}缺少必要交易日：'+','.join(missing))
    return sorted(wanted)
