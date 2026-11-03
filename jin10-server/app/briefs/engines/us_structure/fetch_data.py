"""Public daily series for the US market-structure brief (Yahoo Finance, no account)."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from app.briefs.engines.us_barometer.fetch_data import UA, checked, get

SYMBOLS = {'spx': '%5EGSPC', 'ndx': '%5EIXIC', 'dji': '%5EDJI', 'rut': '%5ERUT', 'spxew': '%5ESPXEW'}


def series(symbol,start=None,end=None,*,require_low=True):
    query='range=10y&interval=1d'
    if start is not None:
        first=int(datetime.combine(start,datetime.min.time(),tzinfo=timezone.utc).timestamp())
        last=int(datetime.combine(end+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc).timestamp())
        query=f'period1={first}&period2={last}&interval=1d'
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}'
    result = json.loads(get(url, {'User-Agent': UA}))['chart']['result'][0]
    stamps = result['timestamp']
    quote = result['indicators']['quote'][0]
    rows = []
    for index, stamp in enumerate(stamps):
        close, low = quote['close'][index], quote['low'][index]
        if close is None or require_low and low is None:
            continue
        row={'date':datetime.fromtimestamp(stamp,tz=timezone.utc).date().isoformat(),'close':round(float(close),4)}
        if require_low and low is not None:row['low']=round(float(low),4)
        rows.append(row)
    return checked(rows, symbol)


def repair(name,rows,day,*,checkpoint=None,attempt_path=None,start=None,max_requests=12):
    from app.briefs.trading_calendar import sessions
    from app.briefs.repair import repair_ranges
    target=datetime.strptime(day,'%Y-%m-%d').date()
    start=start or max(target-timedelta(days=3653),datetime(2015,1,1).date())
    wanted=sessions('xnys',start,target)
    symbol='%5EW5000' if name=='wilshire' else SYMBOLS[name]
    from app.briefs.sources import read,save
    attempts=read(attempt_path) if attempt_path and attempt_path.exists() else {}
    repaired,report=repair_ranges(rows,wanted,
        lambda first,last:series(symbol,first,last,**({'require_low':False} if name=='wilshire' else {})),
        checkpoint=checkpoint,attempts=attempts,max_requests=max_requests,
        attempt_checkpoint=(lambda entries:save(attempt_path,entries)) if attempt_path else None)
    return repaired,report


def fetch(on_series=None):
    result={}
    for name,symbol in SYMBOLS.items():
        result[name]=series(symbol)
        if on_series:on_series(name,result[name])
    return result
