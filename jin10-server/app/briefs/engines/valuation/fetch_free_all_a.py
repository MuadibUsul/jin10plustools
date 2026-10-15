"""Public, bulk all-A capitalization acquisition with explicit source differences.

Eastmoney per-stock TOTAL_MARKET_CAP = A-share close * full company shares.
The complete Shanghai/Shenzhen/Beijing response is summed with Decimal. It is
NOT asserted equal to Legulegu; production source transitions must keep this
provider name and difference metadata. In particular, no correction factor is
applied to make these totals resemble another provider's published totals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CHINA = timezone(timedelta(hours=8))
ENDPOINT = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
PAGE = 'https://data.eastmoney.com/gzfx/detail/600519.html'
PAGE_SIZE = 10000
MAX_PAGES = 5
MAX_BYTES = 4 * 1024 * 1024
SOURCE = 'eastmoney_public_all_a_company_total_capitalization'


class FetchAllAError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _day(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise FetchAllAError('invalid_date', 'Dates must use YYYY-MM-DD.') from None
    if value != parsed.isoformat():
        raise FetchAllAError('invalid_date', 'Dates must use canonical YYYY-MM-DD.')
    return value


def _decimal(value, field):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal, float)):
        raise FetchAllAError('invalid_data', f'{field} must be positive and finite.')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise FetchAllAError('invalid_data', f'{field} must be positive and finite.') from None
    if not number.is_finite() or number <= 0:
        raise FetchAllAError('invalid_data', f'{field} must be positive and finite.')
    return number


def _parse(raw):
    try:
        data = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (UnicodeError, ValueError):
        raise FetchAllAError('invalid_response', 'Valuation source did not return finite JSON.') from None
    if not isinstance(data, dict) or data.get('success') is not True or data.get('code') != 0:
        raise FetchAllAError('source_rejected', 'Valuation source did not return successful data.')
    result = data.get('result')
    if not isinstance(result, dict) or not isinstance(result.get('data'), list):
        raise FetchAllAError('invalid_data', 'Valuation records are missing.')
    for key in ('pages', 'count'):
        if isinstance(result.get(key), bool) or not isinstance(result.get(key), int) or result[key] <= 0:
            raise FetchAllAError('invalid_data', 'Pagination metadata is invalid.')
    if result['pages'] > MAX_PAGES:
        raise FetchAllAError('source_changed', 'Valuation response exceeds the configured pagination limit.')
    return result


def _url(expected_date, previous_date, page):
    params = {'reportName': 'RPT_VALUEANALYSIS_DET',
              'columns': 'SECURITY_CODE,SECUCODE,TRADE_MARKET,TRADE_DATE,TOTAL_MARKET_CAP,TOTAL_SHARES,CLOSE_PRICE',
              'filter': f"(TRADE_DATE>='{previous_date}')(TRADE_DATE<='{expected_date}')",
              'pageNumber': page, 'pageSize': PAGE_SIZE, 'source': 'WEB', 'client': 'WEB',
              'sortColumns': 'TRADE_DATE,SECURITY_CODE', 'sortTypes': '-1,1'}
    return ENDPOINT + '?' + urlencode(params)


def _download(url):
    try:
        with urlopen(Request(url, headers={'User-Agent': 'DailyContentWorkflow/1.0',
                                           'Referer': PAGE}), timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchAllAError('source_changed', 'Valuation source response exceeds its size limit.')
            return raw
    except HTTPError as exc:
        raise FetchAllAError('http_error', f'Valuation source returned HTTP {exc.code}.') from None
    except (URLError, TimeoutError, OSError):
        raise FetchAllAError('network_error', 'Valuation source was not reachable within its time limit.') from None


def normalize_all_a(raw_pages, expected_date, previous_date, reference_yi=None):
    """Validate complete pages and return explicitly labelled totals and audit."""
    expected_date, previous_date = _day(expected_date), _day(previous_date)
    if previous_date >= expected_date:
        raise FetchAllAError('invalid_date', 'Previous session must precede the expected session.')
    pages = [_parse(raw) for raw in raw_pages]
    if not pages or len(pages) != pages[0]['pages']:
        raise FetchAllAError('incomplete_data', 'One or more valuation response pages are missing.')
    if any((p['pages'], p['count']) != (pages[0]['pages'], pages[0]['count']) for p in pages):
        raise FetchAllAError('source_changed', 'Valuation pagination changed during acquisition.')
    records = [row for page in pages for row in page['data']]
    if len(records) != pages[0]['count']:
        raise FetchAllAError('incomplete_data', 'Valuation row count does not match the reported total.')
    values = {previous_date: {}, expected_date: {}}
    exchanges = {previous_date: {'SH': 0, 'SZ': 0, 'BJ': 0}, expected_date: {'SH': 0, 'SZ': 0, 'BJ': 0}}
    for row in records:
        if not isinstance(row, dict):
            raise FetchAllAError('invalid_data', 'Valuation record is not an object.')
        timestamp = row.get('TRADE_DATE')
        if not isinstance(timestamp, str) or len(timestamp) != 19 or timestamp[10:] != ' 00:00:00':
            raise FetchAllAError('invalid_data', 'Valuation record timestamp format changed.')
        day = _day(timestamp[:10])
        if day not in values:
            raise FetchAllAError('unexpected_session', 'Valuation response includes an unrequested session.')
        code, secucode = row.get('SECURITY_CODE'), row.get('SECUCODE')
        if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
            raise FetchAllAError('invalid_data', 'Valuation source returned an invalid security code.')
        exchange = (secucode.rsplit('.', 1)[-1] if isinstance(secucode, str) else '')
        valid = ((exchange == 'SH' and code.startswith('6')) or
                 (exchange == 'SZ' and code.startswith(('0', '3'))) or
                 (exchange == 'BJ' and code.startswith(('4', '8', '920'))))
        if not valid or secucode != code + '.' + exchange:
            raise FetchAllAError('unexpected_security', 'Valuation response includes a non-A-share or an unknown code/exchange.')
        if code in values[day]:
            raise FetchAllAError('invalid_data', 'Valuation response repeats a security/date pair.')
        market_cap = _decimal(row.get('TOTAL_MARKET_CAP'), 'TOTAL_MARKET_CAP')
        close = _decimal(row.get('CLOSE_PRICE'), 'CLOSE_PRICE')
        shares = _decimal(row.get('TOTAL_SHARES'), 'TOTAL_SHARES')
        if shares != shares.to_integral_value() or abs(market_cap - close * shares) > Decimal('0.02'):
            raise FetchAllAError('definition_changed', 'Market cap no longer equals A-share close times integer total company shares.')
        values[day][code] = market_cap
        exchanges[day][exchange] += 1
    rows = []
    comparison = []
    for day in (previous_date, expected_date):
        if any(exchanges[day][exchange] == 0 for exchange in ('SH', 'SZ', 'BJ')):
            raise FetchAllAError('incomplete_data', 'One or more exchanges are absent from a required session.')
        total = sum(values[day].values(), Decimal(0))
        rows.append({'date': day, 'market_cap_cny': str(total), 'market_cap_yi': str(total / Decimal('100000000')),
                     'company_count': len(values[day]), 'exchange_counts': exchanges[day], 'source': SOURCE})
        if reference_yi is not None:
            if day not in reference_yi:
                raise FetchAllAError('missing_reference', 'Reference totals must cover both requested sessions.')
            reference = _decimal(reference_yi[day], 'reference_market_cap_yi')
            difference = total / Decimal('100000000') - reference
            comparison.append({'date': day, 'reference_market_cap_yi': str(reference),
                               'difference_yi': str(difference),
                               'equal_after_reference_2dp_rounding': (total / Decimal('100000000')).quantize(Decimal('.01')) == reference})
    return {'source': SOURCE, 'units': 'CNY', 'scope': 'Shanghai, Shenzhen and Beijing A-share companies returned by the dated provider universe',
            'definition': 'sum(A_share_close * full_company_total_shares)',
            'cross_listed_company_treatment': 'Full company share count priced at A-share close; not A-share-only or mixed A/H price capitalization',
            'legulegu_equivalence': 'not_asserted; provider universe and maintenance can differ',
            'reference_comparison': comparison,
            'may_label_as_legulegu_source_value': False,
            'automatic_source_switch_allowed': False,
            'historical_backfill_2015_complete': False,
            'universe_audit': 'Response completeness is checked against this provider, not independently against all exchanges historical listing rosters',
            'previous_date_rechecked': previous_date, 'expected_date': expected_date, 'rows': rows}


def fetch_all_a(expected_date, previous_date, output_dir, reference_yi=None):
    """One bulk query with only source-required pagination; no cached fallback."""
    expected_date, previous_date = _day(expected_date), _day(previous_date)
    if previous_date >= expected_date:
        raise FetchAllAError('invalid_date', 'Previous session must precede the expected session.')
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CHINA).strftime('%Y%m%dT%H%M%S%f%z')
    raw_pages, evidence = [], []
    page, expected_pages = 1, 1
    while page <= expected_pages:
        url = _url(expected_date, previous_date, page)
        raw = _download(url)
        path = output / f'all-a-{stamp}-page{page}.json'
        path.write_bytes(raw)
        evidence.append({'url': url, 'file': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
        result = _parse(raw)
        if page == 1:
            expected_pages = result['pages']
        raw_pages.append(raw)
        page += 1
    normalized = normalize_all_a(raw_pages, expected_date, previous_date, reference_yi)
    normalized.update(fetched_at=datetime.now(CHINA).isoformat(), raw_evidence=evidence)
    path = output / f'all-a-{stamp}-normalized.json'
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8')
    return normalized


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-date', required=True)
    parser.add_argument('--previous-date', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(fetch_all_a(args.expected_date, args.previous_date, args.output_dir), ensure_ascii=False, indent=2))
    except FetchAllAError as exc:
        print(json.dumps({'status': 'failed', 'code': exc.code, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
