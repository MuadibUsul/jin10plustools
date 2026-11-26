"""Public daily series for the US market-structure brief (Yahoo Finance, no account)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math

import httpx

from app.briefs.engines.us_barometer.fetch_data import UA, checked, get

SYMBOLS = {'spx': '%5EGSPC', 'ndx': '%5EIXIC', 'dji': '%5EDJI', 'rut': '%5ERUT', 'spxew': '%5ESPXEW'}


def series(symbol, start=None, end=None, *, require_low=False, include_low=True, client: httpx.Client | None = None):
    query = 'range=10y&interval=1d'
    if start is not None:
        first = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        last = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
        query = f'period1={first}&period2={last}&interval=1d'
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}'
    raw = get(url, {'User-Agent': UA}, client) if client is not None else get(url, {'User-Agent': UA})
    payload = json.loads(raw)
    results = (payload.get('chart') or {}).get('result') or []
    if not results:
        raise ValueError(f'{symbol} 来源未返回有效数据')
    result = results[0]
    stamps = result.get('timestamp') or []
    quotes = (result.get('indicators') or {}).get('quote') or []
    if not stamps or not quotes:
        raise ValueError(f'{symbol} 来源缺少行情序列')
    quote = quotes[0]
    closes = quote.get('close') or []
    lows = quote.get('low') or []
    rows = []
    for index, stamp in enumerate(stamps):
        close = closes[index] if index < len(closes) else None
        low = lows[index] if index < len(lows) else None
        if close is None or (require_low and low is None):
            continue
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
            raise ValueError(f'{symbol} 收盘价存在无效值')
        if include_low and low is not None:
            if isinstance(low, bool) or not isinstance(low, (int, float)) or not math.isfinite(low) or low <= 0:
                raise ValueError(f'{symbol} 盘中低点存在无效值')
        row = {
            'date': datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat(),
            'close': round(float(close), 4),
        }
        if include_low and low is not None:
            row['low'] = round(float(low), 4)
        rows.append(row)
    return checked(rows, symbol)


def repair(name, rows, day, *, checkpoint=None, attempt_path=None, start=None, max_requests=12):
    from app.briefs.repair import repair_ranges
    from app.briefs.sources import read, save
    from app.briefs.trading_calendar import sessions

    target = datetime.strptime(day, '%Y-%m-%d').date()
    start = start or max(target - timedelta(days=3653), datetime(2015, 1, 1).date())
    wanted = sessions('xnys', start, target)
    symbol = '%5EW5000' if name == 'wilshire' else SYMBOLS[name]
    attempts = read(attempt_path) if attempt_path and attempt_path.exists() else {}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        repaired, report = repair_ranges(
            rows, wanted,
            lambda first, last: series(symbol, first, last, require_low=name == 'rut',
                                       include_low=name != 'wilshire', client=client),
            checkpoint=checkpoint, attempts=attempts, max_requests=max_requests,
            attempt_checkpoint=(lambda entries: save(attempt_path, entries)) if attempt_path else None,
        )
    return repaired, report


def fetch(on_series=None):
    result = {}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for name, symbol in SYMBOLS.items():
            result[name] = series(symbol, require_low=name == 'rut', client=client)
            if on_series:
                on_series(name, result[name])
    return result
