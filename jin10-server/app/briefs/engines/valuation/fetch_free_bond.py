"""Public ChinaBond 10-year treasury *yield curve* acquisition.

This is a candidate change of source, never a substitute silently spliced into
the workbook's Investing benchmark closing-yield series. The normal official
PBC sub-page offers dated HTML history, up to one year per request. No browser,
API key, computed token, date repair, or cached-data fallback is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

METHOD_ID = 'chinabond_treasury_yield_curve'
CURVE_NAME = '中债国债收益率曲线'
ENDPOINT = 'https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery'
SOURCE_PAGE = 'https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/more?locale=cn_ZH'
MAX_BYTES = 4 * 1024 * 1024
CHINA = timezone(timedelta(hours=8))


class BondFetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _day(value: str | date) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            result = date.fromisoformat(value)
            if result.isoformat() == value:
                return result
        except ValueError:
            pass
    raise BondFetchError('invalid_argument', 'Dates must be canonical YYYY-MM-DD.')


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag in ('th', 'td'):
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ('th', 'td') and self.cell is not None:
            if self.row is not None:
                self.row.append(''.join(self.cell).strip())
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def normalize_history(html: str, start_date: str | date,
                      end_date: str | date) -> list[dict]:
    """Validate exact curve identity, explicit row dates and source precision."""
    start, end = _day(start_date), _day(end_date)
    if start > end:
        raise BondFetchError('invalid_argument', 'Start date is after end date.')
    parser = _Tables()
    parser.feed(html)
    headers = [row for row in parser.rows
               if '曲线名称' in row and '日期' in row and '10年' in row]
    if len(headers) != 1:
        raise BondFetchError('source_changed', 'Unique dated ten-year history header was not found.')
    header = headers[0]
    if any(header.count(label) != 1 for label in ('曲线名称', '日期', '10年')):
        raise BondFetchError('source_changed', 'Ambiguous history columns.')
    curve_col, date_col, value_col = (header.index(label)
                                      for label in ('曲线名称', '日期', '10年'))
    result: dict[str, dict] = {}
    for row in parser.rows:
        if len(row) <= curve_col or row[curve_col] != CURVE_NAME:
            continue
        if len(row) != len(header):
            raise BondFetchError('source_changed', 'Curve row does not match its header.')
        try:
            day = _day(row[date_col])
        except BondFetchError:
            raise BondFetchError('invalid_data', 'The source row has no canonical date.') from None
        if day < start or day > end:
            raise BondFetchError('date_mismatch', 'The source returned a row outside the requested interval.')
        raw_value = row[value_col]
        if not re.fullmatch(r'-?\d+(?:\.\d+)?', raw_value):
            raise BondFetchError('invalid_data', 'Yield is missing or is not a bare percent-number.')
        try:
            number = Decimal(raw_value)
        except InvalidOperation:
            raise BondFetchError('invalid_data', 'Yield is not a decimal number.') from None
        if not number.is_finite() or number <= -5 or number >= 30:
            raise BondFetchError('invalid_data', 'Yield is outside the configured sanity bounds.')
        key = day.isoformat()
        if key in result:
            raise BondFetchError('duplicate_date', 'Duplicate source date; no row was selected arbitrarily.')
        result[key] = {
            'date': key,
            'yield_percent_number': float(number),
            'yield_source_text': raw_value,
            'source_decimal_places': max(0, -number.as_tuple().exponent),
            'tenor_years': 10,
            'curve_name': CURVE_NAME,
            'method_id': METHOD_ID,
        }
    if not result:
        raise BondFetchError('pending_data', 'No dated treasury ten-year curve rows are available.')
    return [result[key] for key in sorted(result)]


def _windows(start: date, end: date):
    """Respect the page's one-year maximum, including leap years."""
    while start <= end:
        last = min(end, start + timedelta(days=364))
        yield start, last
        start = last + timedelta(days=1)


def _fetch(start: date, end: date, output_dir: Path, *,
           requested_dates: set[str] | None, timeout: float) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now(CHINA).isoformat()
    run_key = datetime.now(CHINA).strftime('%Y%m%dT%H%M%S%f')
    captures, rows = [], []
    manifest = {
        'method_id': METHOD_ID,
        'source_page': SOURCE_PAGE,
        'source_name': '中国债券信息网 / 央行分网页',
        'series_kind': 'estimated_treasury_yield_curve',
        'is_investing_benchmark_close': False,
        'needs_source_change_approval': True,
        'approval_note': '原工作簿指定 Investing 收盘。本源为中债估值曲线，变更口径需确认并重算全历史，不可拼接。',
        'unit': 'percent_number',
        'erp_conversion': '1 / pe_ttm - yield_percent_number / 100',
        'precision_policy': 'Keep published decimal text; no rounding to match the old workbook.',
        'start_date': start.isoformat(),
        'end_date': end.isoformat(),
        'requested_dates': sorted(requested_dates) if requested_dates is not None else None,
        'captured_at': captured_at,
        'status': 'fetching',
        'captures': captures,
    }
    manifest_path = output_dir / f'bond-{run_key}-manifest.json'
    try:
        for first, last in _windows(start, end):
            params = {'startDate': first.isoformat(), 'endDate': last.isoformat(),
                      'gjqx': '10', 'qxId': 'hzsylqx', 'locale': 'cn_ZH'}
            url = ENDPOINT + '?' + urlencode(params)
            request = Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                           'Accept': 'text/html'})
            try:
                with urlopen(request, timeout=timeout) as response:
                    body = response.read(MAX_BYTES + 1)
                    status = response.status
                    content_type = response.headers.get('Content-Type', '')
            except HTTPError as exc:
                raise BondFetchError('source_access_required' if exc.code in (401, 403)
                                     else 'source_http_error',
                                     f'Official source returned HTTP {exc.code}.') from None
            except (socket.timeout, TimeoutError):
                raise BondFetchError('timeout', 'Official source timed out.') from None
            except URLError:
                raise BondFetchError('network_error', 'Official source could not be reached.') from None
            if len(body) > MAX_BYTES:
                raise BondFetchError('source_changed', 'Official history response exceeds the size limit.')
            raw_path = output_dir / f'chinabond-{run_key}-{first}-{last}.html'
            raw_path.write_bytes(body)
            captures.append({'url': url, 'method': 'GET', 'http_status': status,
                             'content_type': content_type, 'raw_path': str(raw_path.resolve()),
                             'sha256': hashlib.sha256(body).hexdigest(),
                             'requested_start': first.isoformat(), 'requested_end': last.isoformat()})
            try:
                html = body.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise BondFetchError('invalid_response', 'Official HTML is not UTF-8.') from None
            rows.extend(normalize_history(html, first, last))
        by_date = {row['date']: row for row in rows}
        if len(by_date) != len(rows):
            raise BondFetchError('duplicate_date', 'Source windows contain duplicate dates.')
        missing = sorted((requested_dates or set()) - by_date.keys())
        if missing:
            manifest['missing_dates'] = missing
            raise BondFetchError('pending_data', 'Requested dates are missing; no forward fill was applied.')
        normalized = sorted(rows, key=lambda row: row['date'])
        normalized_path = output_dir / f'bond-{run_key}-normalized.json'
        normalized_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest.update(status='ok', record_count=len(rows),
                        normalized_path=str(normalized_path.resolve()))
        return {**manifest, 'normalized': normalized, 'raw': captures,
                'manifest_path': str(manifest_path.resolve())}
    except BondFetchError as exc:
        manifest.update(status=exc.code, error=str(exc))
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


def fetch_bond(dates: str | date | Iterable[str | date], output_dir: str | Path,
               *, timeout: float = 30) -> dict:
    """Re-fetch one or more exact completed dates; missing dates fail explicitly.

    Include the preceding completed dates on each run to detect revisions. The
    returned normalized rows also include available dates between the endpoints.
    Never pass an unfiltered calendar as if every day must have a market quote.
    """
    if isinstance(dates, (str, date)):
        dates = [dates]
    wanted = {_day(item) for item in dates}
    if not wanted:
        raise BondFetchError('invalid_argument', 'At least one date is required.')
    return _fetch(min(wanted), max(wanted), Path(output_dir),
                  requested_dates={item.isoformat() for item in wanted}, timeout=timeout)


def fetch_bond_history(start_date: str | date, end_date: str | date,
                       output_dir: str | Path, *, timeout: float = 30) -> dict:
    """Fetch every published row in an interval, without inventing holiday rows."""
    start, end = _day(start_date), _day(end_date)
    if start > end:
        raise BondFetchError('invalid_argument', 'Start date is after end date.')
    return _fetch(start, end, Path(output_dir), requested_dates=None, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', action='append', dest='dates')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.dates and not (args.start or args.end):
        result = fetch_bond(args.dates, args.output)
    elif not args.dates and args.start and args.end:
        result = fetch_bond_history(args.start, args.end, args.output)
    else:
        parser.error('Use --date (repeatable), or --start with --end.')
    print(json.dumps({key: result[key] for key in ('method_id', 'status', 'record_count',
                    'needs_source_change_approval', 'manifest_path')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
