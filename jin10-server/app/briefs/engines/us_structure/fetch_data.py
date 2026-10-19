"""Public daily series for the US market-structure brief (Yahoo Finance, no account)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.briefs.engines.us_barometer.fetch_data import UA, checked, get

SYMBOLS = {'spx': '%5EGSPC', 'ndx': '%5EIXIC', 'dji': '%5EDJI', 'rut': '%5ERUT', 'spxew': '%5ESPXEW'}


def series(symbol):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1d'
    result = json.loads(get(url, {'User-Agent': UA}))['chart']['result'][0]
    stamps = result['timestamp']
    quote = result['indicators']['quote'][0]
    rows = []
    for index, stamp in enumerate(stamps):
        close, low = quote['close'][index], quote['low'][index]
        if close is None or low is None:
            continue
        rows.append({'date': datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat(),
                     'close': round(float(close), 4), 'low': round(float(low), 4)})
    return checked(rows, symbol)


def fetch():
    return {name: series(symbol) for name, symbol in SYMBOLS.items()}
