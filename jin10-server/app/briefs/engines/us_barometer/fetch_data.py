"""Public sources for the US barometer brief.

Wilshire 5000 daily closes come from Yahoo Finance's public chart endpoint; quarterly US
GDP comes from FRED's CSV export; the S&P 500 equity risk premium comes from StreetStats'
public valuation API using its guest token. No account, cookie or key is used.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from urllib.parse import urlencode

import httpx

UA = 'Mozilla/5.0'  # Yahoo rejects requests without a User-Agent
LIMIT = 8_000_000


def get(url, headers=None):
    """One connection per request with retries.

    FRED's edge stalls browser-style UA/TLS pairs and answers the client default, so the
    GDP request deliberately sends no User-Agent while Yahoo requires one.
    """
    import time
    merged = {'Connection': 'close', **(headers or {})}
    last = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                response = client.get(url, headers=merged)
            response.raise_for_status()
            raw = response.content
            if len(raw) > LIMIT:
                raise ValueError('来源数据过大')
            return raw
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last = error
            time.sleep(1.5 * (attempt + 1))
    raise ValueError('来源连接超时，请稍后重试') from last


def checked(rows, label, key='date'):
    if len(rows) < 2:
        raise ValueError(f'{label}数据不足')
    dates = [row[key] for row in rows]
    if dates != sorted(set(dates)):
        raise ValueError(f'{label}日期重复或未按时间排序')
    return rows


def wilshire():
    """Daily Wilshire 5000 full-cap close; one index point equals one billion USD of market value."""
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EW5000?' + urlencode({'range': '10y', 'interval': '1d'})
    data = json.loads(get(url, {'User-Agent': UA}))
    result = data['chart']['result'][0]
    stamps = result['timestamp']
    closes = result['indicators']['quote'][0]['close']
    rows = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        if not isinstance(close, (int, float)) or not close > 0:
            raise ValueError('美股总市值存在无效值')
        rows.append({'date': date.fromtimestamp(stamp).isoformat(), 'close': round(float(close), 4)})
    return checked(rows, '美股总市值')


def gdp():
    """Quarterly US GDP, seasonally adjusted annual rate, in billions of dollars."""
    raw = get('https://fred.stlouisfed.org/graph/fredgraph.csv?id=GDP').decode('utf-8-sig')
    rows = []
    for line in csv.reader(io.StringIO(raw)):
        if len(line) < 2 or not line[0].startswith('20'):
            continue
        value = line[1].strip()
        if not value or value == '.':
            continue
        rows.append({'period': date.fromisoformat(line[0]).isoformat(), 'value': round(float(value), 3)})
    if any(not row['value'] > 0 for row in rows):
        raise ValueError('GDP 存在无效值')
    return checked(rows, 'GDP', 'period')


def streetstats_erp():
    """S&P 500 equity risk premium history (fraction of 1) from the public market valuation API."""
    token = json.loads(get('https://streetstats.finance/api/token', {'User-Agent': UA}))['token']
    data = json.loads(get('https://streetstats.finance/api/valuation/market/risk',
                          headers={'User-Agent': UA, 'Authorization': f'Bearer {token}'}))
    rows = []
    for item in data.get('riskHistory') or []:
        erp = item.get('ERP')
        if erp is None or erp == '':
            continue
        erp = float(erp)
        if not 0 < erp < 0.5:
            raise ValueError('股权风险溢价存在异常值')
        rows.append({'date': item['Date'], 'erp': round(erp, 6)})
    return checked(rows, '股权风险溢价')
