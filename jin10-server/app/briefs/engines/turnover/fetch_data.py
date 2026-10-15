"""Fetch Legulegu's public daily counts without Codex or a saved API token.

The normal page is visited first. If it publishes a complete data URL, use an
ordinary HTTP session; otherwise a short-lived Playwright browser observes the
data response requested by that page. No login, CAPTCHA solving, token
generation, cached-data fallback, or reverse engineered signing is performed.

Install ``playwright`` and its Chromium browser in the deployment environment.
An existing compatible browser may instead be selected with
TURNOVER_BROWSER_EXECUTABLE or --browser-executable. A storage-state file is
optional and must come from the operator's own authorized browser session.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import http.cookiejar
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

SOURCE = 'https://legulegu.com/stockdata/market-turn-over-ratio-statistics'
DATA_PATH = '/stockdata/market-turn-over-ratio-statistics-data'
ALLOWED_HOSTS = {'legulegu.com', 'www.legulegu.com'}
CHINA = timezone(timedelta(hours=8))
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class FetchError(RuntimeError):
    """A structured, credential-free error suitable for unattended runners."""

    def __init__(self, code: str, message: str, *, snapshot: dict | None = None):
        super().__init__(message)
        self.code = code
        self.snapshot = snapshot


@dataclass
class _Capture:
    body: bytes
    headers: dict[str, str]
    endpoint: str
    transport: str


class _PageMetadata(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == 'meta' and values.get('name'):
            self.meta[str(values['name'])] = str(values.get('content') or '')


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FetchError('timeout', 'Source fetch exceeded its time budget.')
    return remaining


def _is_data_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.hostname in ALLOWED_HOSTS
            and parsed.path == DATA_PATH)


def _public_endpoint(url: str) -> str:
    """Never persist short-lived tokens, CSRF values, or session cookies."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


def _read_http(opener, url: str, deadline: float, headers: dict | None = None) -> _Capture:
    request_headers = {'User-Agent': 'Mozilla/5.0', 'Referer': SOURCE,
                       'Accept': 'application/json,text/html;q=0.9'}
    request_headers.update(headers or {})
    try:
        with opener.open(Request(url, headers=request_headers),
                         timeout=_remaining(deadline)) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise FetchError('source_changed', 'Source response exceeds the size limit.')
            return _Capture(body, {k.lower(): v for k, v in response.headers.items()},
                            response.url, 'http')
    except HTTPError as exc:
        code = 'access_required' if exc.code in (401, 403, 429) else 'network_error'
        raise FetchError(code, f'Source HTTP request returned status {exc.code}.') from None
    except (URLError, TimeoutError, OSError):
        raise FetchError('network_error', 'Source HTTP request did not complete.') from None


def _literal_data_url(page_html: str) -> str | None:
    # Use only a URL actually delivered in the page. A concatenated/computed URL
    # is deliberately left to the real page JavaScript, not reconstructed here.
    pattern = r'''["']([^"'<>\s]*market-turn-over-ratio-statistics-data\?[^"'<>\s]+)["']'''
    for candidate in re.findall(pattern, page_html):
        url = urljoin(SOURCE, html.unescape(candidate))
        token = parse_qs(urlsplit(url).query).get('token', [''])[0]
        if _is_data_url(url) and token and not any(x in token for x in ('{', '}', '+')):
            return url
    return None


def _browser_capture(deadline: float, browser_executable: str | None,
                     storage_state: str | Path | None) -> _Capture:
    try:
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import TimeoutError as BrowserTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise FetchError('dependency_missing',
                         'Dynamic source requires the playwright Python package and Chromium.') from None

    if storage_state is not None and not Path(storage_state).is_file():
        raise FetchError('configuration_error', 'Configured browser storage-state file is missing.')
    browser = None
    try:
        with sync_playwright() as playwright:
            executable = browser_executable or os.environ.get('TURNOVER_BROWSER_EXECUTABLE')
            launch_options: dict = {'headless': True, 'timeout': _remaining(deadline) * 1000}
            if executable:
                launch_options['executable_path'] = executable
            browser = playwright.chromium.launch(**launch_options)
            try:
                options: dict = {'locale': 'zh-CN', 'timezone_id': 'Asia/Shanghai'}
                if storage_state is not None:
                    options['storage_state'] = str(storage_state)
                context = browser.new_context(**options)
                page = context.new_page()
                page.set_default_timeout(_remaining(deadline) * 1000)
                # Observe only the source's own normal request. The temporary
                # URL remains in memory and is never written to the snapshot.
                with page.expect_response(lambda response: _is_data_url(response.url),
                                          timeout=_remaining(deadline) * 1000) as pending:
                    navigation = page.goto(SOURCE, wait_until='domcontentloaded',
                                           timeout=_remaining(deadline) * 1000)
                    if navigation and navigation.status in (401, 403, 429):
                        raise FetchError('access_required',
                                         'Source refused the normal browser visit; operator action is required.')
                response = pending.value
                if response.status != 200:
                    code = 'access_required' if response.status in (401, 403, 429) else 'network_error'
                    raise FetchError(code, f'Source data request returned status {response.status}.')
                body = response.body()
                if len(body) > MAX_RESPONSE_BYTES:
                    raise FetchError('source_changed', 'Source response exceeds the size limit.')
                return _Capture(body, response.headers, response.url, 'browser_response')
            finally:
                browser.close()
    except FetchError:
        raise
    except BrowserTimeout:
        raise FetchError('timeout',
                         'Normal page did not deliver its data in time; check access, login or verification manually.') from None
    except BrowserError:
        # Playwright error strings can contain request URLs. Do not echo them.
        raise FetchError('browser_error',
                         'Browser fetch failed; check the installed Chromium and authorized source access.') from None


def _capture_source(deadline: float, browser_executable: str | None,
                    storage_state: str | Path | None) -> _Capture:
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        page = _read_http(opener, SOURCE, deadline)
        page_html = page.body.decode('utf-8-sig')
        endpoint = _literal_data_url(page_html)
        if endpoint:
            parser = _PageMetadata()
            parser.feed(page_html)
            headers: dict[str, str] = {}
            csrf = parser.meta.get('_csrf')
            csrf_header = parser.meta.get('_csrf_header')
            if csrf and csrf_header and re.fullmatch(r'[A-Za-z0-9_-]+', csrf_header):
                headers[csrf_header] = csrf
            return _read_http(opener, endpoint, deadline, headers)
    except (FetchError, UnicodeDecodeError):
        # A browser's ordinary visit may work when an HTTP-only visit does not.
        # No CAPTCHA solving, stealth flags or alternate signed endpoints.
        _remaining(deadline)
    return _browser_capture(deadline, browser_executable, storage_state)


def _decode_capture(capture: _Capture):
    try:
        return json.loads(capture.body.decode('utf-8-sig'),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))
    except (UnicodeDecodeError, ValueError):
        raise FetchError('invalid_response',
                         'Source returned HTML or invalid JSON instead of public daily statistics.') from None


def _integer(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FetchError('invalid_data', f'Source field {field} must be an integer count.')
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise FetchError('invalid_data', f'Source field {field} must be an integer count.')
    return int(value)


def validate_rows(payload, *, today: date | None = None) -> list[dict]:
    """Validate the observed daily-count schema and return unique ascending rows."""
    if not isinstance(payload, list) or len(payload) < 9:
        raise FetchError('invalid_data', 'Expected at least nine raw daily-count records from the source.')
    today = today or datetime.now(CHINA).date()
    normalized: list[dict] = []
    seen: set[str] = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise FetchError('invalid_data', 'Source contains a record that is not an object.')
        required = ('date', 'totalCompany', 'belowPercent3', 'belowPercent5', 'close')
        if any(field not in raw for field in required):
            raise FetchError('source_changed', 'Source daily-count schema is missing required fields.')
        try:
            trading_date = date.fromisoformat(raw['date'])
        except (TypeError, ValueError):
            raise FetchError('invalid_data', 'Source trading dates must use YYYY-MM-DD.') from None
        day = trading_date.isoformat()
        if day != raw['date'] or trading_date > today:
            raise FetchError('invalid_data', 'Source contains a noncanonical or future trading date.')
        if day in seen:
            raise FetchError('invalid_data', f'Source contains duplicate date {day}.')
        seen.add(day)
        row = dict(raw)
        row['date'] = day
        for field in ('totalCompany', 'belowPermill8', 'belowPercent1', 'belowPercent3',
                      'belowPercent5', 'belowPercent10'):
            if field in row:
                row[field] = _integer(row[field], field)
        total = row['totalCompany']
        if total <= 0 or not 0 <= row['belowPercent3'] <= row['belowPercent5'] <= total:
            raise FetchError('invalid_data', f'Source contains impossible company counts for {day}.')
        for field in ('belowPermill8', 'belowPercent1', 'belowPercent10'):
            if field in row and not 0 <= row[field] <= total:
                raise FetchError('invalid_data', f'Source contains impossible {field} for {day}.')
        close = row['close']
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
            raise FetchError('invalid_data', f'Source contains an invalid index close for {day}.')
        normalized.append(row)
    return sorted(normalized, key=lambda row: row['date'])


def fetch_snapshot(output_dir: str | Path, timeout_seconds: float = 90, *,
                   minimum_latest_date: str | None = None,
                   browser_executable: str | None = None,
                   storage_state: str | Path | None = None) -> dict:
    """Fetch a new source response, validate it and save an auditable snapshot.

    Returns path (normalized JSON list), raw_path, metadata_path, fetched_at,
    source, earliest_date, latest_date, row_count and transport. No historical
    local file is ever used as a fetch fallback. ``minimum_latest_date`` should
    come from the caller's exchange calendar; without it freshness is explicitly
    ``not_checked`` rather than assumed from wall-clock time.
    """
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('timeout_seconds must be positive and finite')
    minimum = None
    if minimum_latest_date is not None:
        try:
            minimum = date.fromisoformat(minimum_latest_date).isoformat()
        except (TypeError, ValueError):
            raise ValueError('minimum_latest_date must use YYYY-MM-DD') from None
    capture = _capture_source(time.monotonic() + timeout_seconds, browser_executable, storage_state)
    rows = validate_rows(_decode_capture(capture))
    fetched = datetime.now(timezone.utc)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=f'legu-{fetched:%Y%m%dT%H%M%SZ}-', dir=destination))
    raw_path = run_dir / 'raw-response.json'
    path = run_dir / 'daily-counts.json'
    metadata_path = run_dir / 'source.json'
    raw_path.write_bytes(capture.body)
    normalized_body = (json.dumps(rows, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
    path.write_bytes(normalized_body)
    latest = rows[-1]['date']
    fresh = minimum is None or latest >= minimum
    result = {
        'path': str(path.resolve()), 'raw_path': str(raw_path.resolve()),
        'metadata_path': str(metadata_path.resolve()),
        'fetched_at': fetched.isoformat().replace('+00:00', 'Z'), 'source': SOURCE,
        'data_endpoint': _public_endpoint(capture.endpoint),
        'source_http_date': capture.headers.get('date'),
        'source_last_modified': capture.headers.get('last-modified'),
        'source_etag': capture.headers.get('etag'),
        'earliest_date': rows[0]['date'], 'latest_date': latest, 'row_count': len(rows),
        'transport': capture.transport, 'minimum_latest_date': minimum,
        'freshness': ('not_checked' if minimum is None else 'current' if fresh else 'not_updated'),
        'raw_sha256': hashlib.sha256(capture.body).hexdigest(),
        'normalized_sha256': hashlib.sha256(normalized_body).hexdigest(),
    }
    metadata_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not fresh:
        raise FetchError('data_not_updated',
                         f'Source latest date is {latest}; expected at least {minimum}.', snapshot=result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--minimum-latest-date')
    parser.add_argument('--browser-executable')
    parser.add_argument('--storage-state', type=Path)
    args = parser.parse_args()
    try:
        result = fetch_snapshot(args.out_dir, args.timeout,
                                minimum_latest_date=args.minimum_latest_date,
                                browser_executable=args.browser_executable,
                                storage_state=args.storage_state)
    except FetchError as exc:
        print(json.dumps({'ok': False, 'code': exc.code, 'message': str(exc),
                          'snapshot': exc.snapshot}, ensure_ascii=False))
        return 2
    print(json.dumps({'ok': True, **result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
