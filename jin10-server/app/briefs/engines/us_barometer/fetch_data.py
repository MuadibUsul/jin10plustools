"""Public sources for the US barometer brief.

Wilshire 5000 daily closes come from Yahoo Finance's public chart endpoint; quarterly US
GDP comes from FRED's CSV export; the S&P 500 equity risk premium comes from StreetStats'
public valuation API using its guest token. No account, cookie or key is used.
"""
from __future__ import annotations

import csv
import io
import json
import os
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from datetime import date,datetime,timezone
from urllib.parse import urlencode

import httpx
from app.briefs.failures import SourceUnavailable

UA = 'Mozilla/5.0'  # Yahoo rejects requests without a User-Agent
LIMIT = 8_000_000
CAPTURE_DIR = ContextVar('us_source_capture_dir', default=None)


@contextmanager
def capture(folder):
    token=CAPTURE_DIR.set(Path(folder))
    try:
        yield
    finally:
        CAPTURE_DIR.reset(token)


def get(url, headers=None):
    """One connection per request with retries.

    FRED's edge stalls browser-style UA/TLS pairs and answers the client default, so the
    GDP request deliberately sends no User-Agent while Yahoo requires one.
    """
    import time
    merged = {'Connection': 'close', **(headers or {})}
    last = None
    for attempt in range(3):
        remaining=float(os.getenv('SOURCE_DEADLINE_MONOTONIC','inf'))-time.monotonic()
        if remaining<=0:raise SourceUnavailable('本次来源读取预算已用完；已保存完成的真实数据，等待下一次重试')
        try:
            with httpx.Client(timeout=min(30,max(.1,remaining)), follow_redirects=True) as client:
                response = client.get(url, headers=merged)
            response.raise_for_status()
            raw = response.content
            if len(raw) > LIMIT:
                raise ValueError('来源数据过大')
            folder=CAPTURE_DIR.get()
            # Guest token replies and request authorization headers are never
            # evidence. Persist only these public numeric source responses.
            from urllib.parse import urlsplit
            path=urlsplit(url).path
            if folder is not None and (path.startswith('/v8/finance/chart/') or
                    path in ('/graph/fredgraph.csv','/api/valuation/market/risk')):
                folder.mkdir(parents=True,exist_ok=True)
                extension='.csv' if path.endswith('.csv') else '.json'
                name='us-source-'+hashlib.sha256(url.encode()).hexdigest()[:16]+extension
                (folder/name).write_bytes(raw)
                metadata={'host':urlsplit(url).hostname,'path':path,
                          'captured_at':datetime.now(timezone.utc).isoformat(),
                          'status_code':response.status_code,'sha256':hashlib.sha256(raw).hexdigest(),
                          'response_date':response.headers.get('date'),
                          'cache_age':response.headers.get('age'),
                          'content_type':response.headers.get('content-type')}
                (folder/(name+'.metadata.json')).write_text(json.dumps(metadata),encoding='utf-8')
            return raw
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in (408,429,500,502,503,504):
                raise SourceUnavailable(f'来源返回 HTTP {error.response.status_code}',source=response.url.host) from None
            last = error
            delay=min(1.5*(attempt+1),max(0,float(os.getenv('SOURCE_DEADLINE_MONOTONIC','inf'))-time.monotonic()))
            time.sleep(delay)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last = error
            time.sleep(min(1.5 * (attempt + 1),max(0,float(os.getenv('SOURCE_DEADLINE_MONOTONIC','inf'))-time.monotonic())))
    raise SourceUnavailable('来源连接失败或暂不可用，请稍后重试') from last


def checked(rows, label, key='date'):
    if len(rows) < 2:
        raise ValueError(f'{label}数据不足')
    dates = [row[key] for row in rows]
    if dates != sorted(set(dates)):
        raise ValueError(f'{label}日期重复或未按时间排序')
    return rows


def wilshire():
    """Daily Wilshire index close, in index points; NOT a verified USD market cap."""
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
