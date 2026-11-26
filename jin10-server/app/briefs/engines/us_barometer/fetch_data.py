"""Public sources for the US barometer brief.

Wilshire 5000 daily closes come from Yahoo Finance's public chart endpoint; quarterly US
GDP comes from FRED's CSV export; the S&P 500 equity risk premium comes from StreetStats'
public valuation API using its guest token. No account, cookie or key is used.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import io
import json
import math
import os
import time
from urllib.parse import urlencode

import httpx

UA = 'Mozilla/5.0'  # Yahoo rejects requests without a User-Agent
LIMIT = 8_000_000
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class _RetryableStatusError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f'HTTP {status_code}')
        self.status_code = status_code


def _stream_response(client: httpx.Client, url: str, headers: dict, timeout: float) -> bytes:
    with client.stream('GET', url, headers=headers, timeout=timeout) as response:
        if response.status_code in RETRYABLE_STATUS:
            raise _RetryableStatusError(response.status_code)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ValueError(f'来源请求失败（HTTP {error.response.status_code}）') from None
        chunks = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > LIMIT:
                raise ValueError('来源数据过大')
            chunks.append(chunk)
        return b''.join(chunks)


def get(url, headers=None, client: httpx.Client | None = None):
    """Fetch source bytes with streaming size cap and transient-error retries.

    FRED's edge stalls browser-style UA/TLS pairs and answers the client default, so the
    GDP request deliberately sends no User-Agent while Yahoo requires one.
    """
    merged = dict(headers or {}) if client is not None else {'Connection': 'close', **(headers or {})}
    last_error = None
    last_status = None
    for attempt in range(3):
        remaining = float(os.getenv('SOURCE_DEADLINE_MONOTONIC', 'inf')) - time.monotonic()
        if remaining <= 0:
            raise ValueError('本次来源读取预算已用完；已保存完成的真实数据，等待下一次重试')
        req_timeout = min(30.0, max(0.1, remaining))
        try:
            if client is not None:
                return _stream_response(client, url, merged, req_timeout)
            with httpx.Client(timeout=req_timeout, follow_redirects=True) as temp_client:
                return _stream_response(temp_client, url, merged, req_timeout)
        except _RetryableStatusError as error:
            last_status = error.status_code
            last_error = error
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last_error = error
        wait_budget = max(0.0, float(os.getenv('SOURCE_DEADLINE_MONOTONIC', 'inf')) - time.monotonic())
        if attempt < 2 and wait_budget > 0:
            time.sleep(min(1.5 * (attempt + 1), wait_budget))
    if last_status is not None:
        raise ValueError(f'来源服务暂时不可用（HTTP {last_status}），请稍后重试') from None
    raise ValueError('来源连接超时，请稍后重试') from last_error


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
    results = (data.get('chart') or {}).get('result') or []
    if not results:
        raise ValueError('美股总市值来源未返回有效数据')
    result = results[0]
    stamps = result.get('timestamp') or []
    quotes = (result.get('indicators') or {}).get('quote') or []
    if not stamps or not quotes:
        raise ValueError('美股总市值来源缺少行情序列')
    closes = quotes[0].get('close') or []
    rows = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
            raise ValueError('美股总市值存在无效值')
        day = datetime.fromtimestamp(stamp, tz=timezone.utc).date().isoformat()
        rows.append({'date': day, 'close': round(float(close), 4)})
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
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError('GDP 存在无效值')
        rows.append({'period': date.fromisoformat(line[0]).isoformat(), 'value': round(parsed, 3)})
    return checked(rows, 'GDP', 'period')


def streetstats_erp():
    """S&P 500 equity risk premium history (fraction of 1) from the public market valuation API."""
    token_payload = json.loads(get('https://streetstats.finance/api/token', {'User-Agent': UA}))
    token = token_payload.get('token') if isinstance(token_payload, dict) else None
    if not token or not isinstance(token, str):
        raise ValueError('股权风险溢价公开令牌无效')
    data = json.loads(get('https://streetstats.finance/api/valuation/market/risk',
                          headers={'User-Agent': UA, 'Authorization': f'Bearer {token}'}))
    rows = []
    for item in data.get('riskHistory') or []:
        if not isinstance(item, dict):
            continue
        erp = item.get('ERP')
        if erp is None or erp == '':
            continue
        if isinstance(erp, bool):
            raise ValueError('股权风险溢价存在异常值')
        erp = float(erp)
        if not math.isfinite(erp) or not 0 < erp < 0.5:
            raise ValueError('股权风险溢价存在异常值')
        raw_day = item.get('Date')
        if raw_day is None or str(raw_day).strip() == '':
            raise ValueError('股权风险溢价缺少日期')
        try:
            day = date.fromisoformat(str(raw_day).strip()[:10]).isoformat()
        except ValueError as exc:
            raise ValueError('股权风险溢价日期格式异常') from exc
        rows.append({'date': day, 'erp': round(erp, 6)})
    return checked(rows, '股权风险溢价')
