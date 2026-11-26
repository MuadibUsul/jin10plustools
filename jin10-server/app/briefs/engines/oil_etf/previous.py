"""Archive a public, dated futures-holdings snapshot for a missing prior day.

CompaniesMarketCap exposes its latest holdings without a login.  It does not
promise a historical endpoint, so a dated copy is only usable when the page's
own as-of date matches the day requested.  The original HTML is retained.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen
from uuid import uuid4

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


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def parse(ticker: str, raw: bytes, expected_day: str | None = None) -> dict:
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
    if expected_day is not None and source_day != expected_day:
        raise PreviousSourceError(f'公开持仓页为{source_day}，需要{expected_day}')
    parser = _Rows()
    parser.feed(html)
    matches = [row for row in parser.rows if len(row) >= 5 and row[1].startswith(NAMES[ticker])]
    if len(matches) > 1:
        # Filter out rolled-out zero-quantity stub rows when an active contract row is present.
        active = [row for row in matches if row[-1].replace(',', '') != '0']
        if active:
            matches = active
    if len(matches) != 1 or not re.fullmatch(SYMBOLS[ticker], matches[0][2]):
        raise PreviousSourceError(f'{ticker}期货合约行不唯一或代码异常')
    quantity = matches[0][-1].replace(',', '')
    if not quantity.isdigit() or int(quantity) <= 0:
        raise PreviousSourceError(f'{ticker}期货合约数量异常')
    return {
        'ticker': ticker,
        'data_date': source_day,
        'contract': matches[0][2],
        'quantity_contracts': int(quantity),
        'futures_barrels': str(int(quantity) * 1000),
        'source_url': PAGES[ticker],
        'raw_sha256': hashlib.sha256(raw).hexdigest(),
    }


def _archive_day(archive: Path, day: str, day_funds: dict[str, dict], day_raws: dict[str, bytes]) -> dict:
    folder = archive / day
    manifest = folder / 'manifest.json'
    existing_funds: dict[str, dict] = {}
    if manifest.exists():
        try:
            loaded = json.loads(manifest.read_text(encoding='utf-8'))
            for ticker, record in (loaded.get('funds') or {}).items():
                html_path = folder / f'{ticker}.html'
                if ticker in PAGES and html_path.is_file() and parse(ticker, html_path.read_bytes(), day) == record:
                    existing_funds[ticker] = record
        except (OSError, ValueError, KeyError, TypeError):
            existing_funds = {}
    folder.mkdir(parents=True, exist_ok=True)
    for ticker, raw in day_raws.items():
        (folder / f'{ticker}.html').write_bytes(raw)
    merged = {**existing_funds, **day_funds}
    data = {
        'data_date': day,
        'provider': 'CompaniesMarketCap public holdings',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'funds': merged,
    }
    _write_json(manifest, data)
    return data


def fetch(day: str, archive: Path, opener=urlopen, *, tickers=None) -> dict:
    """Use a verified archive, or capture today's public page(s) dated *day*."""
    date.fromisoformat(day)
    wanted = set(tickers) if tickers is not None else None
    if wanted is not None and (not wanted or not wanted <= set(PAGES)):
        raise PreviousSourceError('未知基金')
    folder = archive / day
    manifest = folder / 'manifest.json'
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding='utf-8'))
        archived_funds = data.get('funds') or {}
        if not archived_funds or not set(archived_funds) <= set(PAGES):
            raise PreviousSourceError('历史持仓归档不完整')
        if wanted is not None and not wanted <= set(archived_funds):
            missing_tickers = sorted(wanted - set(archived_funds))
        else:
            missing_tickers = []
        for ticker in archived_funds:
            html_path = folder / f'{ticker}.html'
            if not html_path.is_file() or parse(ticker, html_path.read_bytes(), day) != archived_funds[ticker]:
                raise PreviousSourceError('历史持仓归档校验失败')
        if not missing_tickers:
            return data
    by_day_funds: dict[str, dict[str, dict]] = {}
    by_day_raws: dict[str, dict[str, bytes]] = {}
    mismatches: list[str] = []
    query_tickers = tuple(wanted) if wanted is not None else tuple(PAGES)
    for ticker in query_tickers:
        url = PAGES[ticker]
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with opener(request, timeout=20) as response:
                raw = response.read(1_000_001)
        except (OSError, TimeoutError) as exc:
            raise PreviousSourceError(f'{ticker}公开持仓页不可访问') from exc
        if len(raw) > 1_000_000:
            raise PreviousSourceError(f'{ticker}公开持仓页过大')
        parsed = parse(ticker, raw, None)
        source_day = parsed['data_date']
        by_day_funds.setdefault(source_day, {})[ticker] = parsed
        by_day_raws.setdefault(source_day, {})[ticker] = raw
        if source_day != day:
            mismatches.append(f'{ticker}公开持仓页为{source_day}，需要{day}')
    for source_day, day_funds in by_day_funds.items():
        _archive_day(archive, source_day, day_funds, by_day_raws[source_day])
    if wanted is not None and mismatches:
        raise PreviousSourceError('；'.join(mismatches))
    if day not in by_day_funds:
        raise PreviousSourceError('；'.join(mismatches) if mismatches else f'公开持仓页未返回{day}数据')
    return json.loads((archive / day / 'manifest.json').read_text(encoding='utf-8'))
