"""Dated numeric market observations, independent of published news copy.

Eastmoney's daily 000001/399001 fields are verified against complete Jin10
market observations before filling a gap. 399106 is deliberately NOT used:
its volume/amount scope differs from the original Shenzhen series.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .failures import SourceContractError, SourceUnavailable, MissingObservations
from .trading_calendar import is_session

SOURCE = 'eastmoney_hushen_index_turnover_yi_v1'
PAGE = 'https://quote.eastmoney.com/center/hszs.html'
ENDPOINT = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
MARKETS = {'sh': ('1.000001', '上证指数'), 'sz': ('0.399001', '深证成指')}


def read_market(url, folder, name):
    from .sources import save
    for attempt in range(3):
        remaining = float(os.getenv('SOURCE_DEADLINE_MONOTONIC', 'inf')) - time.monotonic()
        if remaining <= 0:
            raise SourceUnavailable('成交额行情读取预算已用完', source=SOURCE)
        try:
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': PAGE})
            with urlopen(request, timeout=min(15, max(.1, remaining))) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise SourceContractError('成交额行情响应超出大小限制', source=SOURCE)
            (folder / name).write_bytes(raw)
            save(folder / (name + '.metadata.json'), {'url': url, 'sha256': hashlib.sha256(raw).hexdigest(),
                 'captured_at': datetime.now(timezone.utc).isoformat()})
            return raw
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise SourceUnavailable(f'成交额行情 HTTP {exc.code}', source=SOURCE) from None
        except OSError:
            pass
        if attempt < 2:
            remaining = float(os.getenv('SOURCE_DEADLINE_MONOTONIC', 'inf')) - time.monotonic()
            if remaining > 0:
                time.sleep(min(.3 * (attempt + 1), remaining))
    raise SourceUnavailable('成交额独立行情读取失败；已保留成功的原始响应', source=SOURCE)


def parse_market(raw, market, start, end):
    try:
        payload = json.loads(raw, parse_float=Decimal)
        data = payload['data']
        code, name = MARKETS[market]
        if payload['rc'] != 0 or not isinstance(data, dict):
            raise ValueError('接口未返回成功数据')
        if data['code'] != code.split('.')[1] or data['market'] != int(code[0]) or data['name'] != name:
            raise ValueError('行情代码或市场不符')
        if not isinstance(data['klines'], list):
            raise ValueError('日线结构变化')
        rows = {}
        for line in data['klines']:
            fields = line.split(',')
            if len(fields) != 11:
                raise ValueError('日线字段数量变化')
            day = date.fromisoformat(fields[0]).isoformat()
            if not start <= day <= end or day in rows or not is_session('xshg', day):
                raise ValueError('日线日期重复或超出请求区间')
            # f56: hands; f57: yuan. Preserve the published decimal precision.
            volume, amount = Decimal(fields[5]), Decimal(fields[6])
            if not all(value.is_finite() and value > 0 for value in (volume, amount)):
                raise ValueError('成交金额或成交量无效')
            rows[day] = {'turn': amount / Decimal(100_000_000), 'vol': volume / Decimal(1_000_000)}
        return rows
    except (ValueError, TypeError, KeyError, ArithmeticError, AttributeError) as exc:
        raise SourceContractError('成交额独立行情结构核验失败：' + str(exc), source=SOURCE) from None


def recover_dates(missing, known, folder, cutoff, checkpoint):
    """At most two months per attempt; validate both markets and every overlap."""
    from .sources import save
    missing = sorted(set(missing))
    months = sorted({day[:7] for day in missing}, reverse=True)[:2]
    known = {row['date']: row for row in known}
    recovered = []
    for month in months:
        start = date.fromisoformat(month + '-01')
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = min(date.fromisoformat(cutoff), next_month - timedelta(days=1))
        captures = {}
        hashes = {}
        for market, (code, _) in MARKETS.items():
            url = ENDPOINT + '?' + urlencode({'secid': code, 'klt': '101', 'fqt': '0',
                'beg': start.strftime('%Y%m%d'), 'end': end.strftime('%Y%m%d'), 'lmt': '30',
                'fields1': 'f1,f2,f3,f4,f5,f6', 'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'})
            name = f'volume-market-{month}-{market}.json'
            raw = read_market(url, folder, name)
            captures[market] = parse_market(raw, market, start.isoformat(), end.isoformat())
            hashes[market] = hashlib.sha256(raw).hexdigest()
        if set(captures['sh']) != set(captures['sz']):
            raise SourceContractError('沪深独立行情日期集合不一致', source=SOURCE)
        combined = {day: {'date': day, 'turn': captures['sh'][day]['turn'] + captures['sz'][day]['turn'],
                         'vol': captures['sh'][day]['vol'] + captures['sz'][day]['vol']}
                    for day in captures['sh']}
        overlaps = sorted(set(known) & set(combined))
        conflicts = []
        for day in overlaps:
            if (abs(Decimal(str(known[day]['turn'])) - combined[day]['turn']) > Decimal('.00000002')
                    or abs(Decimal(str(known[day]['vol'])) - combined[day]['vol']) > Decimal('.00000001')):
                conflicts.append(day)
        report = {'source': SOURCE, 'month': month, 'overlap_dates': overlaps,
                  'conflicting_dates': conflicts, 'minimum_overlap': 5, 'raw_sha256': hashes}
        save(folder / f'volume-market-{month}-comparison.json', report)
        if conflicts or len(overlaps) < 5:
            raise SourceContractError('独立行情与原始收盘序列不一致或不足5日可比重叠；停止补数', source=SOURCE)
        for day in missing:
            if day not in combined:
                continue
            row = {**combined[day], 'source_kind': 'eastmoney_daily_index', 'source_url': PAGE,
                   'source_method': SOURCE, 'source_raw_sha256': hashes,
                   'source_overlap_dates': overlaps, 'source_components': {
                       'sh_turn': captures['sh'][day]['turn'], 'sh_vol': captures['sh'][day]['vol'],
                       'sz_turn': captures['sz'][day]['turn'], 'sz_vol': captures['sz'][day]['vol']},
                   'source_precision': {'amount_yi': 'source_decimal', 'volume_yi_shares': 'source_decimal'}}
            checkpoint(row)
            recovered.append(row)
    remaining = sorted(set(missing) - {row['date'] for row in recovered})
    if remaining:
        raise MissingObservations('成交额独立行情仍缺少必要日期：' + ','.join(remaining),
                                  source=SOURCE, missing_dates=remaining)
    return recovered
