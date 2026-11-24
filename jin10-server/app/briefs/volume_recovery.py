"""Recover absent Jin10 daily rows from its public, dated closing flash.

Both public listing JSON and the publisher's original HTML are retained. Amounts
keep the published decimal precision. Volume is converted from 亿手 to 亿股
(100 shares per hand), with the original rounded inputs retained explicitly.
"""
from datetime import date
from decimal import Decimal
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .failures import SourceContractError, SourceUnavailable
from .trading_calendar import previous_session

HEADERS = {'x-app-id': 'bVBF4FyRTn5NJF5n', 'x-version': '1.0.0',
           'Referer': 'https://www.jin10.com/', 'User-Agent': 'Mozilla/5.0'}
NUMBER = r'(\d+(?:\.\d+)?)'


class Description(HTMLParser):
    def __init__(self):
        super().__init__()
        self.content = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name') == 'description':
            self.content = attrs.get('content')


def _read(url, folder, name, deadline):
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceUnavailable('金十收盘快讯补取达到读取时限', source='jin10_closing_flash')
        try:
            with urlopen(Request(url, headers=HEADERS), timeout=min(10, remaining)) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise SourceContractError('金十收盘快讯响应超过大小限制', source='jin10_closing_flash')
            (folder / name).write_bytes(raw)
            return raw
        except HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt:
                raise SourceUnavailable(f'金十收盘快讯读取返回 HTTP {exc.code}', source='jin10_closing_flash') from None
        except (URLError, TimeoutError, OSError):
            if attempt:
                raise SourceUnavailable('金十收盘快讯读取失败，请检查请求及网络', source='jin10_closing_flash') from None


def parse_closing(text, day, previous):
    """Require a full closing report plus an independently retained prior day."""
    text = unescape(re.sub(r'<[^>]+>', '', text))
    parsed_day = date.fromisoformat(day)
    if f'{parsed_day.month}月{parsed_day.day}日' not in text:
        raise SourceContractError('收盘快讯正文日期不符', source='jin10_closing_flash')
    total = re.search('沪深两市全天成交额合计' + NUMBER + '亿元', text)
    markets = []
    for market in ('沪', '深'):
        pattern = (market + r'市成交额为' + NUMBER + r'亿元[（(]上一交易日为' + NUMBER
                   + r'亿元[）)]，成交量为' + NUMBER + r'亿手[（(]上一交易日为'
                   + NUMBER + r'亿手[）)]')
        match = re.search(pattern, text)
        if match is None:
            raise SourceContractError('收盘快讯缺少完整沪深分项或成交量单位', source='jin10_closing_flash')
        markets.append(match.groups())
    if total is None or any(Decimal(value) <= 0 for market in markets for value in market):
        raise SourceContractError('收盘快讯金额或成交量无效', source='jin10_closing_flash')
    amount = Decimal(total[1])
    if sum(Decimal(market[0]) for market in markets) != amount:
        raise SourceContractError('收盘快讯沪深分项与总额不一致', source='jin10_closing_flash')
    if previous is None or previous['date'] != previous_session('xshg', day).isoformat():
        raise SourceContractError('缺少可独立核对的前一交易日记录，停止快讯补取', source='jin10_closing_flash')
    previous_amount = sum(Decimal(market[1]) for market in markets)
    amount_tolerance = sum(Decimal(1).scaleb(Decimal(market[1]).as_tuple().exponent) for market in markets) / 2
    if abs(Decimal(str(previous['turn'])) - previous_amount) > amount_tolerance:
        raise SourceContractError('收盘快讯前日金额与原序列不一致，停止跨来源拼接', source='jin10_closing_flash')
    prior_volume = sum(Decimal(market[3]) for market in markets)
    # Two individually rounded market volumes can differ from their exact sum by
    # at most half the sum of their stated decimal resolutions.
    tolerance = sum(Decimal(1).scaleb(Decimal(market[3]).as_tuple().exponent) for market in markets) / 2
    if abs(Decimal(str(previous['vol'])) / 100 - prior_volume) > tolerance:
        raise SourceContractError('收盘快讯成交量单位与原序列不一致', source='jin10_closing_flash')
    return {'date': day, 'turn': str(amount),
            'vol': str(sum(Decimal(market[2]) for market in markets) * 100),
            'source_precision': {'amount_yi': str(Decimal(1).scaleb(amount.as_tuple().exponent)),
                'sh_volume_yi_hands': markets[0][2], 'sz_volume_yi_hands': markets[1][2],
                'volume_conversion': '亿手×100=亿股'},
            'source_kind': 'jin10_published_closing_flash'}


def recover_dates(days, rows, folder, on_point):
    """Bounded public lookup for at most three absent observations per attempt."""
    from .sources import save
    deadline = min(float(os.getenv('SOURCE_DEADLINE_MONOTONIC', 'inf')), time.monotonic() + 45)
    known = {row['date']: row for row in rows}
    recovered = []
    for day in sorted(days)[:3]:
        cursor = day + ' 15:30:00'
        for page in range(1, 7):
            name = f'volume-flash-{day}-list-{page}.json'
            url = 'https://flash-api.jin10.com/get_flash_list?' + urlencode(
                {'max_time': cursor, 'channel': -8200, 'vip': 1})
            try:
                payload = json.loads(_read(url, folder, name, deadline))
            except (ValueError, UnicodeError) as exc:
                raise SourceContractError('金十快讯列表未返回有效JSON', source='jin10_closing_flash') from exc
            if not isinstance(payload, dict):
                raise SourceContractError('金十快讯列表结构变化', source='jin10_closing_flash')
            entries = payload.get('data')
            if payload.get('status') != 200 or not isinstance(entries, list):
                raise SourceContractError('金十快讯列表结构变化', source='jin10_closing_flash')
            if not entries:
                break
            candidate = None
            for entry in entries:
                content = (entry.get('data') or {}).get('content', '')
                stamp = entry.get('time', '')
                if stamp.startswith(day) and stamp[11:] >= '15:00:00' and '沪深两市全天成交额合计' in content:
                    if (entry.get('data') or {}).get('exclusive_to'):
                        continue
                    candidate = entry
                    break
            if candidate:
                ident = str(candidate.get('id', ''))
                if not re.fullmatch(day.replace('-', '') + r'\d{12}', ident):
                    raise SourceContractError('金十收盘快讯编号日期不符', source='jin10_closing_flash')
                source_url = 'https://flash.jin10.com/detail/' + ident
                source_file = f'volume-flash-{day}-original.html'
                raw = _read(source_url, folder, source_file, deadline)
                document = Description()
                document.feed(raw.decode('utf-8'))
                if not document.content:
                    raise SourceContractError('金十收盘原文缺少完整公开正文', source='jin10_closing_flash')
                previous = known.get(previous_session('xshg', day).isoformat())
                point = parse_closing(document.content, day, previous)
                listed = parse_closing(candidate['data']['content'], day, previous)
                if point != listed:
                    raise SourceContractError('金十原文与列表数值不一致，需核查修订', source='jin10_closing_flash')
                point.update(source_url=source_url, source_file=source_file,
                             source_sha256=hashlib.sha256(raw).hexdigest())
                on_point(point)
                known[day] = point
                recovered.append(point)
                break
            next_cursor = entries[-1].get('time', '')
            if next_cursor >= cursor or next_cursor < day + ' 15:00:00':
                break
            cursor = next_cursor
    save(folder / 'volume-flash-recovery.json', {'requested_dates': sorted(days),
         'recovered_dates': [row['date'] for row in recovered],
         'sources': [row['source_url'] for row in recovered]})
    return recovered
