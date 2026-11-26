"""Regression tests for refactored chart renderers, image delivery, and brief engines."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import matplotlib
from matplotlib._pylab_helpers import Gcf
from PIL import Image, ImageDraw
import pytest

from app.briefs.ai_compute_charts import token_chart
from app.briefs.charts import FONT, us_barometer_chart, valuation_chart
from app.briefs.delivery import FIXED_NOTES, OIL_CLOSING
from app.briefs.editorial_market import build_context
from app.briefs.engines.liquidity import delivery as liquidity_delivery
from app.briefs.engines.liquidity import run as liquidity_run
from app.briefs.engines.liquidity.copy_tracks import build_copy
from app.briefs.engines.liquidity.render import Poster, render_poster
from app.briefs.engines.oil_etf import delivery as oil_delivery
from app.briefs.engines.oil_etf import previous as oil_previous
from app.briefs.engines.oil_etf import run as oil_run
from app.briefs.engines.turnover import delivery as turnover_delivery
from app.briefs.engines.turnover import fetch_data as turnover_fetch
from app.briefs.engines.us_barometer import fetch_data as barometer_fetch
from app.briefs.engines.us_structure import fetch_data as structure_fetch
from app.briefs.engines.us_structure.calculate import structure
from app.briefs.engines.valuation import delivery as valuation_delivery
from app.briefs.engines.valuation import fetch_free_all_a
from app.briefs.font_cmap import font_cmap
from app.briefs.glyph_atlas import _cached_atlas, draw_date_glyphs
from app.briefs.trading_calendar import sessions
from app.services import image_delivery


def test_wilshire_utc_evening_close_does_not_shift_to_next_day_on_utc_plus_8(monkeypatch):
    # 2026-10-14 21:00:00 UTC is 2026-10-15 05:00:00 in Asia/Shanghai (UTC+8).
    stamps = [
        int(datetime(2026, 10, 13, 21, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc).timestamp()),
    ]
    payload = {'chart': {'result': [{'timestamp': stamps, 'indicators': {'quote': [{'close': [75100.0, 76581.52]}]}}]}}
    monkeypatch.setattr(barometer_fetch, 'get', lambda *a, **kw: json.dumps(payload).encode())
    rows = barometer_fetch.wilshire()
    assert [row['date'] for row in rows] == ['2026-10-13', '2026-10-14']


def test_yahoo_null_result_and_nonfinite_values_are_rejected_cleanly(monkeypatch):
    monkeypatch.setattr(barometer_fetch, 'get', lambda *a, **kw: b'{"chart":{"result":null}}')
    monkeypatch.setattr(structure_fetch, 'get', lambda *a, **kw: b'{"chart":{"result":null}}')
    with pytest.raises(ValueError, match='未返回有效数据'):
        barometer_fetch.wilshire()
    with pytest.raises(ValueError, match='未返回有效数据'):
        structure_fetch.series('%5EGSPC')

    bad_erp_responses = iter([
        b'{"token":"guest-token"}',
        b'{"riskHistory":[{"Date":"2026-10-13T00:00:00Z","ERP":0.05},{"Date":"2026-10-14T16:00:00Z","ERP":0.052}]}',
    ])
    monkeypatch.setattr(barometer_fetch, 'get', lambda *a, **kw: next(bad_erp_responses))
    assert [row['date'] for row in barometer_fetch.streetstats_erp()] == ['2026-10-13', '2026-10-14']


def test_http_streaming_cap_retry_and_client_reuse(monkeypatch):
    monkeypatch.setattr(barometer_fetch.time, 'sleep', lambda *_: None)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith('/oversize'):
            return httpx.Response(200, content=b'x' * 128)
        if request.url.path.endswith('/forbidden'):
            return httpx.Response(403, content=b'denied')
        if len(calls) == 1:
            return httpx.Response(429, content=b'rate limit')
        return httpx.Response(200, content=b'{"ok":true}')

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        assert barometer_fetch.get('https://example.test/retry', client=client) == b'{"ok":true}'
        assert len(calls) == 2

        with pytest.raises(ValueError, match='HTTP 403'):
            barometer_fetch.get('https://example.test/forbidden', client=client)

    client_creations = 0
    stamps = [
        int(datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc).timestamp()),
    ]
    sample_chart = json.dumps({'chart': {'result': [{
        'timestamp': stamps,
        'indicators': {'quote': [{'close': [100.0, 101.0], 'low': [99.0, 100.0]}]},
    }]}}).encode()
    real_client = httpx.Client

    def tracked_client(*args, **kwargs):
        nonlocal client_creations
        client_creations += 1
        kwargs['transport'] = httpx.MockTransport(lambda req: httpx.Response(200, content=sample_chart))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(structure_fetch.httpx, 'Client', tracked_client)
    fetched = structure_fetch.fetch()
    assert set(fetched) == set(structure_fetch.SYMBOLS)
    assert client_creations == 1

    with httpx.Client(transport=transport) as client:
        monkeypatch.setattr(barometer_fetch, 'LIMIT', 64)
        with pytest.raises(ValueError, match='过大'):
            barometer_fetch.get('https://example.test/oversize', client=client)


def _us_structure_year_fixtures(day_str: str = '2026-11-10'):
    days = sessions('xnys', '2025-11-03', day_str)
    gaps = {d: 1000.0 for d in days}
    gaps['2025-11-20'] = 700.0  # Low in Nov of the PRIOR year (2025-11), not 2026-11!
    gaps['2026-07-15'] = 1400.0
    gaps[days[-2]] = 850.0
    gaps[days[-1]] = 880.0  # gap_up = True

    def make_index(base_close: float, last_close: float):
        return [
            {'date': d, 'close': last_close if d == days[-1] else base_close, 'low': base_close - 10}
            for d in days
        ]

    rut = []
    for d in days:
        close = 3000.0
        low = 2800.0
        if d == '2025-11-21':
            low = 2650.0
        elif d == '2026-05-20':
            close = 3006.0  # Peak close: 3000 / 3006 - 1 = -0.20% (< 0.5% drawdown!)
        elif d == days[-2]:
            close = 2970.0
            low = 2750.0
        elif d == days[-1]:
            # Rally day: close rises and today_low (2790) > yesterday_low (2750), while > 2650!
            close = 3000.0
            low = 2790.0
        rut.append({'date': d, 'close': close, 'low': low})

    spx = make_index(7800.0, 7750.0)
    return {
        'spx': spx,
        'ndx': make_index(26000.0, 25900.0),
        'dji': make_index(52000.0, 51950.0),
        'spxew': [{'date': row['date'], 'close': row['close'] + gaps[row['date']],
                   'low': row['close'] + gaps[row['date']]} for row in spx],
        'rut': rut,
    }


def test_us_structure_rally_day_prior_year_month_and_sub_half_percent_drawdown():
    day = '2026-11-10'
    data = _us_structure_year_fixtures(day)
    result = structure(day, data)
    body = result['body']

    # 1. Rally day (today_low 2790 > yesterday_low 2750) must NOT claim a multi-month low!
    assert '新低' not in body and '低点' not in body
    assert result['facts']['rut_low_since_date'] == ''
    assert result['facts']['rut_low_since_month'] is None

    # 2. Prior-year November (2025-11-20) must NOT be called "本月" in November 2026!
    assert '本月' not in body
    assert '从近一年低位回升' in body

    # 3. Drawdown of -0.20% must NOT round to "回调了0%"!
    assert '回调了0%' not in body
    assert '回调了0.20%' in body

    # Downstream editorial context validator must also accept the facts cleanly.
    ctx = build_context('us_structure', {'data_date': day, 'facts': result['facts']})
    assert ctx['scenario'] == 'breadth_weak'

    # Cross-year intraday low break (today_low 2700 < yesterday_low 2750, > 2650 on 2025-11-21) includes year.
    data['rut'][-1]['low'] = 2700.0
    cross_year = structure(day, data)
    assert '一度刷新2025年11月以来新低' in cross_year['body']
    assert cross_year['facts']['rut_low_since_date'] == '2025-11-21'
    assert cross_year['facts']['rut_low_since_month'] == 11

    # Missing trading session at the start of the 1-year window is detected.
    first_in_window = next(row['date'] for row in data['spxew'] if row['date'] >= '2025-11-10')
    gapped = {**data, 'spxew': [row for row in data['spxew'] if row['date'] != first_in_window]}
    with pytest.raises(ValueError, match=first_in_window):
        structure(day, gapped)


def test_font_cmap_reads_ttc_collection_and_poster_falls_back_cleanly(tmp_path):
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.ttCollection import TTCollection

    ttc_path = tmp_path / 'bundle.ttc'
    collection = TTCollection()
    collection.fonts = [TTFont(str(FONT)), TTFont(str(FONT))]
    collection.save(str(ttc_path))
    for f in collection.fonts:
        f.close()

    font_cmap.cache_clear()
    cmap = font_cmap(str(ttc_path))
    assert cmap is not None and ord('0') in cmap
    assert font_cmap(str(ttc_path)) is cmap
    assert font_cmap.cache_info().hits >= 1

    bad_path = tmp_path / 'corrupt.ttc'
    bad_path.write_bytes(b'not-a-font')
    assert font_cmap(str(bad_path)) is None

    # Poster.glyph_font must never crash on tabs, zero-width spaces, or rare symbols.
    poster = Poster(600)
    for ch in ('\t', '\u200b', '\U0001f680', '☺'):
        chosen = poster.glyph_font(ch, 32, bold=True)
        assert chosen is not None
    poster.text((40, 40), '流动性\t测试\u200b文本☺', 32)
    assert '流动性 测试文本☺' in poster.texts


def test_glyph_atlas_caches_across_date_labels(tmp_path):
    _cached_atlas.cache_clear()
    assets = Path(turnover_delivery.__file__).resolve().parent / 'assets'
    canvas = Image.new('RGB', (200, 60), (4, 12, 15))
    for index in range(12):
        draw_date_glyphs(canvas, (100, 20), f'2026/10/{index + 1}', assets_dir=assets, x_offset=-3)
    info = _cached_atlas.cache_info()
    assert info.misses == 1 and info.hits == 11


def test_image_delivery_alpha_watermark_signature_and_lazy_quantize(tmp_path, monkeypatch):
    # 1. Transparent RGBA PNG on dark=False composites over white (#FFFFFF), never black!
    rgba_path = tmp_path / 'transparent.png'
    rgba = Image.new('RGBA', (800, 600), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).rectangle((200, 150, 600, 450), fill=(20, 100, 220, 255))
    rgba.save(rgba_path)

    light_composed = image_delivery.compose_image(rgba_path, dark=False)
    assert light_composed.getpixel((10, 10)) == (255, 255, 255)
    assert light_composed.getpixel((300, 300)) == (20, 100, 220)

    dark_composed = image_delivery.compose_image(rgba_path, dark=True)
    assert dark_composed.getpixel((10, 10)) == (11, 13, 18)

    # 2. delivery_signature() changes immediately when WATERMARK bytes change.
    sig_default = image_delivery.delivery_signature()
    alt_watermark = tmp_path / 'alt-watermark.png'
    Image.new('RGBA', (200, 60), (255, 0, 0, 255)).save(alt_watermark)
    monkeypatch.setattr(image_delivery, 'WATERMARK', alt_watermark)
    sig_alt = image_delivery.delivery_signature()
    assert sig_alt != sig_default

    # 3. encode_image skips quantization when raw PNG already fits <= MAX_BYTES.
    small = Image.new('RGB', (600, 400), 'white')
    with patch.object(Image.Image, 'quantize', wraps=small.quantize) as quantize_spy:
        content, suffix, media = image_delivery.encode_image(small)
        assert suffix == '.png' and media == 'image/png' and len(content) <= image_delivery.MAX_BYTES
        assert quantize_spy.call_count == 0

    # 4. download_name normalizes bare and dotted suffixes identically.
    assert image_delivery.download_name('2026-10-16', '测试标题', 'png') == image_delivery.download_name('2026-10-16', '测试标题', '.png')


def test_liquidity_multi_tenor_oil_zero_stub_delivery_unification_and_turnover_429(tmp_path, monkeypatch):
    # Multi-tenor PBC reverse repo (both 1D and 7D on the same day).
    snapshot = {
        'quotes': {
            'updated_at': '2026-10-30 17:00',
            'quote_date': '2026-10-30',
            'timestamp_kind': 'session_close',
            'rows': [
                {'product': 'DR001', 'weighted_rate': '1.2884', 'latest_rate': '1.2800'},
                {'product': 'DR007', 'weighted_rate': '1.4535', 'latest_rate': '1.4500'},
            ],
        },
        'history': {'rows': [{'date': '2026-10-29', 'DR001': '1.2800', 'DR007': '1.4400'}]},
        'central_bank': {
            'date': '2026-10-30',
            'term_days': 1,
            'rate_percent': '1.25',
            'bid_yi': '5410',
            'awarded_yi': '5410',
            'operations': [
                {'term_days': 1, 'rate_percent': '1.25', 'bid_yi': '5410', 'awarded_yi': '5410'},
                {'term_days': 7, 'rate_percent': '1.40', 'bid_yi': '100', 'awarded_yi': '100'},
            ],
        },
    }
    copy = build_copy(snapshot)
    assert 'DR001较当日隔夜逆回购操作利率1.25%高3.84个基点' in copy['body']
    assert 'DR007较当日7天期逆回购操作利率1.40%高5.35个基点' in copy['body']
    assert '5410亿元隔夜逆回购' in copy['body'] and '100亿元7天期逆回购' in copy['body']

    # CFETS holiday-aware default_expected_date (Monday after National Day break).
    china = timezone(timedelta(hours=8))
    assert liquidity_run.default_expected_date(datetime(2026, 10, 9, 9, 0, tzinfo=china)) == '2026-10-08'

    # Oil ETF: reject 0-quantity non-zero marketvalue; skip 0-quantity $0 stub when active contract exists.
    price_raw = json.dumps([{'symbol': 'BNO', 'displaydate': '2026-10-14', 'nav': '30.1', 'navtotal': '1000000'}]).encode()
    bad_holdings = json.dumps([{
        'fundsymbol': 'BNO', 'fullpartial': 'full', 'asofdate': '2026-10-14',
        'holdingtype': 'Futures', 'identifiertodisplay': 'COZ6', 'name': 'BRENT CRUDE FUTR Dec26',
        'possessionname': 'Hold', 'shares': 0, 'contractprice': '75.0', 'marketvalue': '75000',
    }]).encode()
    with pytest.raises(oil_run.DataError, match='Zero-quantity'):
        oil_run.normalize('BNO', bad_holdings, price_raw)

    rolled_holdings = json.dumps([
        {
            'fundsymbol': 'BNO', 'fullpartial': 'full', 'asofdate': '2026-10-14',
            'holdingtype': 'Futures', 'identifiertodisplay': 'COX6', 'name': 'BRENT CRUDE FUTR Nov26',
            'possessionname': 'Hold', 'shares': 0, 'contractprice': '74.5', 'marketvalue': '0',
        },
        {
            'fundsymbol': 'BNO', 'fullpartial': 'full', 'asofdate': '2026-10-14',
            'holdingtype': 'Futures', 'identifiertodisplay': 'COZ6', 'name': 'BRENT CRUDE FUTR Dec26',
            'possessionname': 'Hold', 'shares': 120, 'contractprice': '75.0', 'marketvalue': '9000000',
        },
    ]).encode()
    normalized = oil_run.normalize('BNO', rolled_holdings, price_raw)
    assert normalized['futures_barrels'] == '120000' and len(normalized['futures_contracts']) == 1

    # Mixed dates in oil_etf.previous.fetch archive each fund under its own source date.
    pages = {
        'USO': (b'<div>Etf holdings as of <span>October 14, 2026</span></div>'
                b'<table><tr><td>1</td><td>WTI CRUDE FUTURE</td><td>CLZ6</td><td>USD</td><td>15000</td></tr></table>'),
        'BNO': (b'<div>Etf holdings as of <span>October 13, 2026</span></div>'
                b'<table><tr><td>1</td><td>BRENT CRUDE FUTR</td><td>COZ6</td><td>USD</td><td>6900</td></tr></table>'),
    }

    class _Resp:
        def __init__(self, body): self._body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, _n=-1): return self._body

    archive_dir = tmp_path / 'oil-archive'
    captured_13 = oil_previous.fetch(
        '2026-10-13', archive_dir,
        opener=lambda req, timeout=20: _Resp(pages['USO' if 'united-states-oil' in req.full_url else 'BNO']),
    )
    assert set(captured_13['funds']) == {'BNO'}
    assert (archive_dir / '2026-10-14' / 'manifest.json').is_file()

    # All 4 engine delivery modules share canonical FIXED_NOTES and inline OIL_CLOSING.
    for mod in (liquidity_delivery, oil_delivery, turnover_delivery, valuation_delivery):
        assert mod.FIXED_NOTES == FIXED_NOTES
        assert mod.complete_body('oil_etf', 'USO持仓为100万桶。') == 'USO持仓为100万桶。' + OIL_CLOSING

    # fetch_free_all_a has sys imported for CLI error reporting.
    assert hasattr(fetch_free_all_a, 'sys')

    # Turnover: HTTP 429 must NOT launch headless Chromium.
    browser_called = False

    def fail_if_browser(*a, **kw):
        nonlocal browser_called
        browser_called = True
        raise AssertionError('Chromium should not launch after HTTP 429')

    monkeypatch.setattr(turnover_fetch, '_read_http', lambda *a, **kw: (_ for _ in ()).throw(
        turnover_fetch.FetchError('access_required', 'HTTP 429', status_code=429)))
    monkeypatch.setattr(turnover_fetch, '_browser_capture', fail_if_browser)
    with pytest.raises(turnover_fetch.FetchError) as exc_info:
        turnover_fetch._capture_source(1e12, None, None)
    assert exc_info.value.status_code == 429 and not browser_called


def test_concurrent_chart_rendering_neither_mutates_rcparams_nor_leaks_figures(tmp_path):
    Gcf.destroy_all()
    rc_before = dict(matplotlib.rcParams)

    days = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(45)]
    buffett_cn = [{'date': d, 'marketCap': str(82 + (i % 5)), 'gdp': '100', 'close': str(3800 + i * 10),
                   'source': 'eastmoney'} for i, d in enumerate(days)]
    # Include a duplicate same-date same-source row and an extreme ERP reading (> 8.0) to exercise guards.
    buffett_cn_dup = buffett_cn + [dict(buffett_cn[-1])]
    erp_cn = [{'date': d, 'erp_percent': '8.4' if i == len(days) - 1 else str(5.2 + (i % 4) * 0.3),
               'close': str(3800 + i * 10), 'pe_source': 'danjuan', 'bond_source': 'chinabond'}
              for i, d in enumerate(days)]
    us_buffett = [{'date': d, 'pct': 180.0 + (i % 6)} for i, d in enumerate(days)]
    us_erp = [{'date': d, 'pct': 4.8 + (i % 5) * 0.1} for i, d in enumerate(days)]
    token_rows = [{'date': d, 'token_price': 1.05 + (i % 7) * 0.03} for i, d in enumerate(days)]
    liq_snapshot = {
        'quotes': {
            'quote_date': days[-1],
            'updated_at': f'{days[-1]} 17:00',
            'rows': [
                {'product': 'DR001', 'weighted_rate': '1.3100', 'latest_rate': '1.3100', 'average_term_days': '1'},
                {'product': 'DR007', 'weighted_rate': '1.4200', 'latest_rate': '1.4200', 'average_term_days': '7'},
            ],
        },
        'history': {
            'start': days[0],
            'end': days[-1],
            'series': ['DR001', 'DR007'],
            'rows': [{'date': d, 'DR001': str(1.25 + (i % 5) * 0.02), 'DR007': str(1.38 + (i % 5) * 0.02)}
                     for i, d in enumerate(days)],
        },
    }

    def job(idx: int) -> Path:
        out = tmp_path / f'worker-{idx}'
        out.mkdir()
        kind = idx % 4
        if kind == 0:
            return valuation_chart(buffett_cn_dup, erp_cn, out)
        if kind == 1:
            return us_barometer_chart(us_buffett, us_erp, out)
        if kind == 2:
            return token_chart(token_rows, out, notes='1、算力价格测试注释。')
        png, _, _ = render_poster(liq_snapshot, out, logo_path=image_delivery.WATERMARK)
        return png

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(job, range(8)))

    for path in paths:
        assert path.is_file()
        with Image.open(path) as rendered:
            assert rendered.width >= 1000 and rendered.height >= 600

    assert Gcf.figs == {}
    assert dict(matplotlib.rcParams) == rc_before
