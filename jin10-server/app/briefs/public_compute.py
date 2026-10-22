"""Read only the four visible summary cards on Silicon Data's public site.

Called by an editor's compute-brief production job. It does not query portal
pages, private endpoints, embedded Next.js data, or chart APIs.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.robotparser import RobotFileParser

import httpx
from lxml import html

ORIGIN = 'https://www.silicondata.com'
ROBOT = ORIGIN + '/robots.txt'
USER_AGENT = 'PLUSBriefFetch/1.0 (editor-triggered public summary reader)'
MAX_HTML = 750_000
MAX_ROBOTS = 30_000
PAGES = {
    'token_cost': {'token': 'llm-token-expenditure-index'},
    'gpu_rental': {'a100': 'a100', 'h100': 'h100', 'b200': 'b200'},
}
HEADINGS = {
    'token': 'LLM Token Expenditure Index',
    'a100': 'A100 Rental Price Index',
    'h100': 'H100 Rental Price Index',
    'b200': 'B200 Rental Price Index',
}
UNITS = {'token': 'USD / M tokens', 'a100': 'USD / GPU-hour',
         'h100': 'USD / GPU-hour', 'b200': 'USD / GPU-hour'}
DATE = re.compile(r'^As of ([A-Za-z]+ \d{1,2}, \d{4})$')
WEEK = re.compile(r'^(\d+(?:\.\d+)?)% \(7D\)$')


def _bounded(client: httpx.Client, url: str, limit: int) -> bytes:
    try:
        with client.stream('GET', url) as response:
            if response.status_code in (403, 429):
                raise ValueError('来源网站拒绝本次访问，请稍后由编辑重试；不会自动重试或绕过限制')
            if response.status_code != 200:
                raise ValueError(f'来源网页暂不可用（HTTP {response.status_code}）')
            if url != ROBOT and 'text/html' not in response.headers.get('content-type', ''):
                raise ValueError('来源网页返回格式已变化')
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > limit:
                    raise ValueError('来源网页大小超出读取限制')
                chunks.append(chunk)
            return b''.join(chunks)
    except httpx.HTTPError:
        raise ValueError('来源网页暂时无法访问，请稍后重试') from None


def _parse(raw: bytes, metric: str, url: str, today: date) -> dict:
    try:
        tree = html.fromstring(raw)
        headings = [' '.join(node.itertext()).strip() for node in tree.xpath('//h1')]
        if not any(HEADINGS[metric] in heading for heading in headings):
            raise ValueError('来源网页指标身份不匹配')
        candidates = [node for node in tree.xpath('//p')
                      if DATE.fullmatch(' '.join(node.itertext()).strip())]
        if len(candidates) != 1:
            raise ValueError('来源网页没有唯一可见的指标日期')
        date_node = candidates[0]
        source_day = datetime.strptime(DATE.fullmatch(' '.join(date_node.itertext()).strip()).group(1),
                                       '%b %d, %Y').date()
        if source_day > today:
            raise ValueError('来源网页展示了未来数据日')
        card = date_node.getparent()
        # The price, unit and 7D change must be siblings of the visible date.
        cards = card.xpath('./div')
        if len(cards) != 2:
            raise ValueError('来源网页价格卡结构已变化')
        price_parts = [' '.join(p.itertext()).strip() for p in cards[0].xpath('./p')]
        if len(price_parts) != 2 or price_parts[1] != UNITS[metric]:
            raise ValueError('来源网页价格单位或结构不匹配')
        price = Decimal(price_parts[0])
        if not price.is_finite() or price <= 0 or price >= 1000000:
            raise ValueError('来源网页价格无效')
        week_parts = [' '.join(p.itertext()).strip() for p in cards[1].xpath('./p')]
        matched = WEEK.fullmatch(week_parts[0]) if len(week_parts) == 1 else None
        if matched is None:
            raise ValueError('来源网页七日变化格式已变化')
        change = Decimal(matched.group(1))
        if not change.is_finite() or change > 1000:
            raise ValueError('来源网页七日变化无效')
        arrows = cards[1].xpath('./svg[contains(@class, "rotate-180")]')
        base_arrow = cards[1].xpath('./svg/path[@d="M4 12 0.5 6.8h2.3V0h2.4v6.8h2.3Z"]')
        if len(base_arrow) != 1 or len(cards[1].xpath('./svg')) != 1:
            raise ValueError('来源网页涨跌方向无法核对')
        direction = 'flat' if change == 0 else 'up' if arrows else 'down'
        signed = change if direction != 'down' else -change
        return {'metric': metric, 'date': source_day.isoformat(),
                'price': format(price, 'f'),
                'precision': max(0, -price.as_tuple().exponent),
                'week_change_pct': format(signed, 'f'),
                'week_direction': direction, 'source_url': url,
                'source_status': 'public_summary'}
    except (InvalidOperation, UnicodeError, OverflowError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError('来源网页可见数字无法核对') from None


def fetch_visible(category: str, *, today: date | None = None,
                  client: httpx.Client | None = None) -> dict:
    """One robots read and one GET per relevant visible page; never retry."""
    if category not in PAGES:
        raise ValueError('仅AI算力CPI和AI景气度指标支持公开页面读取')
    today = today or datetime.now(timezone.utc).date()
    owned_client = client is None
    if client is None:
        client = httpx.Client(timeout=15, follow_redirects=False,
                              headers={'User-Agent': USER_AGENT, 'Accept': 'text/html'})
    try:
        robots = _bounded(client, ROBOT, MAX_ROBOTS).decode('utf-8-sig')
        if not re.search(r'(?im)^user-agent\s*:', robots):
            raise ValueError('来源网站robots规则无法核对，本次停止读取')
        parser = RobotFileParser()
        parser.parse(robots.splitlines())
        readings = []
        for metric, slug in PAGES[category].items():
            url = f'{ORIGIN}/products/silicon-index/{slug}'
            if not parser.can_fetch(USER_AGENT, url):
                raise ValueError('来源网站robots规则不允许读取该页面')
            readings.append(_parse(_bounded(client, url, MAX_HTML), metric, url, today))
        if category == 'gpu_rental' and len({row['date'] for row in readings}) != 1:
            raise ValueError('三张GPU公开卡片的数据日不一致，本次不生成稿件')
        return {'category': category, 'data_date': readings[0]['date'],
                'captured_at': datetime.now(timezone.utc).isoformat(), 'readings': readings}
    finally:
        if owned_client:
            client.close()
