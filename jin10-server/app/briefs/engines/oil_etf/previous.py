"""Archive a public, dated futures-holdings snapshot for a missing prior day.

CompaniesMarketCap exposes its latest holdings without a login.  It does not
promise a historical endpoint, so a dated copy is only usable when the page's
own as-of date matches the day requested.  The original HTML is retained.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

PAGES = {
    'USO': 'https://companiesmarketcap.com/united-states-oil-fund-lp/holdings/',
    'BNO': 'https://companiesmarketcap.com/united-states-brent-oil-fund-lp/holdings/',
}
NAMES = {'USO': 'WTI CRUDE FUTURE', 'BNO': 'BRENT CRUDE FUTR'}
SYMBOLS = {'USO': r'CL[FGHJKMNQUVXZ]\d{1,2}', 'BNO': r'CO[FGHJKMNQUVXZ]\d{1,2}'}


class PreviousSourceError(ValueError):
    pass


class _Rows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        elif tag == 'td' and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def parse(ticker: str, raw: bytes, expected_day: str) -> dict:
    if ticker not in PAGES:
        raise PreviousSourceError('未知基金')
    try:
        html = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise PreviousSourceError('历史持仓页面编码异常') from exc
    match = re.search(r'Etf holdings as of\s*<span[^>]*>([A-Za-z]+ \d{1,2}, \d{4})</span>', html)
    if not match:
        raise PreviousSourceError('历史持仓页面缺少数据日期')
    try:
        source_day = datetime.strptime(match.group(1), '%B %d, %Y').date().isoformat()
    except ValueError as exc:
        raise PreviousSourceError('历史持仓日期格式异常') from exc
    if source_day != expected_day:
        raise PreviousSourceError(f'公开持仓页为{source_day}，需要{expected_day}')
    parser = _Rows()
    parser.feed(html)
    matches = [row for row in parser.rows if len(row) >= 5 and row[1].startswith(NAMES[ticker])]
    if len(matches) != 1 or not re.fullmatch(SYMBOLS[ticker], matches[0][2]):
        raise PreviousSourceError(f'{ticker}期货合约行不唯一或代码异常')
    quantity = matches[0][-1].replace(',', '')
    if not quantity.isdigit() or int(quantity) <= 0:
        raise PreviousSourceError(f'{ticker}期货合约数量异常')
    return {'ticker': ticker, 'data_date': source_day, 'contract': matches[0][2],
            'quantity_contracts': int(quantity), 'futures_barrels': str(int(quantity) * 1000),
            'source_url': PAGES[ticker], 'raw_sha256': hashlib.sha256(raw).hexdigest()}


def fetch(day: str, archive: Path, opener=urlopen) -> dict:
    """Use a verified archive, or capture today's public page if dated *day*."""
    date.fromisoformat(day)
    folder = archive / day
    manifest = folder / 'manifest.json'
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if set(data['funds']) != set(PAGES):
            raise PreviousSourceError('历史持仓归档不完整')
        for ticker in PAGES:
            if parse(ticker, (folder / f'{ticker}.html').read_bytes(), day) != data['funds'][ticker]:
                raise PreviousSourceError('历史持仓归档校验失败')
        return data
    raws = {}
    funds = {}
    for ticker, url in PAGES.items():
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with opener(request, timeout=20) as response:
                raw = response.read(1_000_001)
        except (OSError, TimeoutError) as exc:
            raise PreviousSourceError(f'{ticker}公开持仓页不可访问') from exc
        if len(raw) > 1_000_000:
            raise PreviousSourceError(f'{ticker}公开持仓页过大')
        funds[ticker] = parse(ticker, raw, day)
        raws[ticker] = raw
    folder.mkdir(parents=True, exist_ok=True)
    for ticker, raw in raws.items():
        (folder / f'{ticker}.html').write_bytes(raw)
    data = {'data_date': day, 'provider': 'CompaniesMarketCap public holdings',
            'captured_at': datetime.now(timezone.utc).isoformat(), 'funds': funds}
    temp = manifest.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(manifest)
    return data
