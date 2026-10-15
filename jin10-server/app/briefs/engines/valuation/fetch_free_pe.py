"""Fetch public CSI 300 PE-TTM without an account, model, or browser.

The provider is Snowball Fund (formerly Danjuan). Its published formula is
SUM(component total market capitalization) / SUM(component TTM net income),
including loss-making constituents. This is the total-market-cap weighted
method family, not equal-weight PE or static PE. It is NOT asserted to be
numerically identical to Lixinger. Caller must record a source change explicitly
before combining it with a pre-existing history.

The public ``day=all`` response is downsampled. It currently contains the latest
two sessions, which are required here; this module never interpolates missing
dates and must not be used to reconstruct a complete daily history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CHINA = timezone(timedelta(hours=8))
HISTORY_URL = 'https://danjuanfunds.com/djapi/index_eva/pe_history/SH000300?day=all'
DETAIL_URL = 'https://danjuanfunds.com/djapi/index_eva/detail/SH000300'
PAGE_URL = 'https://danjuanfunds.com/djmodule/value-center?channel=1300100135'
METHOD_URL = 'https://xueqiu.com/9485866208/128488024'
MAX_BYTES = 2 * 1024 * 1024


class FetchPEError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _day(value):
    if not isinstance(value, str):
        raise FetchPEError('invalid_date', 'Dates must use YYYY-MM-DD.')
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise FetchPEError('invalid_date', 'Dates must use YYYY-MM-DD.') from None
    if value != parsed.isoformat():
        raise FetchPEError('invalid_date', 'Dates must use YYYY-MM-DD.')
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal, float, str)):
        raise FetchPEError('invalid_data', 'PE must be a positive finite number.')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise FetchPEError('invalid_data', 'PE must be a positive finite number.') from None
    if not number.is_finite() or number <= 0:
        raise FetchPEError('invalid_data', 'PE must be a positive finite number.')
    return number


def _timestamp_day(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise FetchPEError('invalid_data', 'Source timestamp must be integer milliseconds.')
    try:
        return datetime.fromtimestamp(value / 1000, CHINA).date().isoformat()
    except (ValueError, OSError, OverflowError):
        raise FetchPEError('invalid_data', 'Source timestamp is outside the supported range.') from None


def _parse_json(raw):
    try:
        result = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (UnicodeError, ValueError):
        raise FetchPEError('invalid_response', 'Source did not return finite UTF-8 JSON.') from None
    if not isinstance(result, dict) or result.get('result_code') != 0 or not isinstance(result.get('data'), dict):
        raise FetchPEError('source_rejected', 'Source reported an error or changed its response schema.')
    return result['data']


def _download(url):
    try:
        with urlopen(Request(url, headers={'User-Agent': 'DailyContentWorkflow/1.0',
                                          'Accept': 'application/json'}), timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchPEError('source_changed', 'Source response exceeds the size limit.')
            return raw
    except HTTPError as exc:
        raise FetchPEError('http_error', f'PE source returned HTTP {exc.code}.') from None
    except (URLError, TimeoutError, OSError):
        raise FetchPEError('network_error', 'PE source could not be reached within its time limit.') from None


def normalize_pe(history_raw, detail_raw, expected_date, previous_date):
    """Validate raw public replies; return only the two requested sessions."""
    expected_date, previous_date = _day(expected_date), _day(previous_date)
    if previous_date >= expected_date:
        raise FetchPEError('invalid_date', 'Previous session must precede the expected session.')
    history = _parse_json(history_raw)
    detail = _parse_json(detail_raw) if detail_raw is not None else None
    records = history.get('index_eva_pe_growths')
    if not isinstance(records, list) or not records:
        raise FetchPEError('invalid_data', 'PE history is missing or empty.')
    values = {}
    for row in records:
        if not isinstance(row, dict):
            raise FetchPEError('invalid_data', 'PE history row is not an object.')
        day = _timestamp_day(row.get('ts'))
        if day in values:
            raise FetchPEError('invalid_data', 'PE history contains duplicate session dates.')
        values[day] = _number(row.get('pe'))
    source_latest = max(values)
    if detail is not None:
        if detail.get('index_code') != 'SH000300':
            raise FetchPEError('invalid_data', 'Detail response is not for CSI 300.')
        if _timestamp_day(detail.get('ts')) != source_latest or _number(detail.get('pe')) != values[source_latest]:
            raise FetchPEError('source_inconsistent', 'PE latest-detail and history responses disagree.')
    missing = sorted({expected_date, previous_date} - values.keys())
    if missing:
        raise FetchPEError('missing_session', 'PE source lacks required sessions: ' + ', '.join(missing))
    return {
        'source': 'snowball_fund_danjuan_public',
        'source_index_code': 'SH000300',
        'units': 'times',
        'method': 'sum_component_total_market_cap_divided_by_sum_ttm_parent_net_income_including_losses',
        'method_family': 'total_market_cap_weighted_PE_TTM',
        'method_evidence': [PAGE_URL, METHOD_URL],
        'source_latest_date': source_latest,
        'source_history_points': len(values),
        'source_history_downsampled': True,
        'detail_crosscheck_performed': detail is not None,
        'lixinger_history_equivalence': 'not_asserted; preserve uploaded history and explicitly record provider transition',
        'expected_date': expected_date,
        'previous_date_rechecked': previous_date,
        'rows': [{'date': day, 'pe_ttm_mcw_raw': str(values[day]),
                  'source': 'snowball_fund_danjuan_public'}
                 for day in (previous_date, expected_date)],
    }


def _completed_cache(output, expected_date, previous_date, verify_detail):
    """Reuse an already completed exact-date capture, never a failed fetch fallback."""
    for path in sorted(output.glob('pe-*-normalized.json'), reverse=True):
        try:
            cached = json.loads(path.read_text(encoding='utf-8'))
            if (cached.get('source') != 'snowball_fund_danjuan_public'
                    or cached.get('expected_date') != expected_date
                    or cached.get('previous_date_rechecked') != previous_date
                    or (verify_detail and not cached.get('detail_crosscheck_performed'))):
                continue
            if {r['date'] for r in cached['rows']} != {expected_date, previous_date}:
                continue
            for row in cached['rows']:
                _number(row['pe_ttm_mcw_raw'])
            evidence = cached.get('raw_evidence', [])
            if not evidence:
                continue
            if any(not Path(item['file']).is_file() or
                   hashlib.sha256(Path(item['file']).read_bytes()).hexdigest() != item['sha256'] for item in evidence):
                continue
            return {**cached, 'completed_date_cache_reused': True,
                    'cache_normalized_path': str(path.resolve()), 'network_requests_this_call': 0}
        except (OSError, ValueError, KeyError, TypeError, FetchPEError):
            continue
    return None


def fetch_pe(expected_date, previous_date, output_dir, *, verify_detail=False, use_completed_cache=True):
    """Fetch both sessions in one batch, or reuse an exact-date successful capture.

    ``verify_detail=True`` is for initialization or an explicit source audit;
    routine daily acquisition makes only the history request. A network failure
    never triggers loading an older-date snapshot.
    """
    _day(expected_date)
    _day(previous_date)
    if previous_date >= expected_date:
        raise FetchPEError('invalid_date', 'Previous session must precede the expected session.')
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if use_completed_cache:
        cached = _completed_cache(output, expected_date, previous_date, verify_detail)
        if cached is not None:
            return cached
    fetched_at = datetime.now(CHINA).isoformat()
    stamp = datetime.now(CHINA).strftime('%Y%m%dT%H%M%S%f%z')
    evidence = []
    bodies = []
    requests = [('history', HISTORY_URL)] + ([('detail', DETAIL_URL)] if verify_detail else [])
    for name, url in requests:
        raw = _download(url)
        path = output / f'pe-{stamp}-{name}.json'
        path.write_bytes(raw)
        evidence.append({'url': url, 'file': str(path.resolve()),
                         'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
        bodies.append(raw)
    result = normalize_pe(bodies[0], bodies[1] if verify_detail else None, expected_date, previous_date)
    result.update({'fetched_at': fetched_at, 'raw_evidence': evidence,
                   'completed_date_cache_reused': False, 'network_requests_this_call': len(requests),
                   'access': {'account_required': False, 'api_key_required': False,
                              'transport': 'ordinary_https', 'browser_required': False}})
    (output / f'pe-{stamp}-normalized.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--previous-date', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--verify-detail', action='store_true', help='Initialization audit only: additionally cross-check latest detail.')
    parser.add_argument('--force-refresh', action='store_true', help='Explicitly bypass the already-successful exact-date capture.')
    args = parser.parse_args()
    try:
        result = fetch_pe(args.date, args.previous_date, args.output_dir,
                          verify_detail=args.verify_detail, use_completed_cache=not args.force_refresh)
    except FetchPEError as exc:
        print(json.dumps({'status': 'failed', 'code': exc.code, 'message': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
