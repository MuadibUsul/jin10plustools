"""Current pledged-repo board stays current; the daily curve and the policy date do not slip back."""
import json
import os
import time
from datetime import datetime
from decimal import Decimal

import pytest

from app.briefs.engines.liquidity.copy_tracks import build_copy
from app.briefs.engines.liquidity.fetch_data import fetch_snapshot, parse_quotes
from app.briefs.engines.liquidity.render import render_poster
from app.services.image_delivery import WATERMARK


def board(stamp, rows):
    return json.dumps({'head': {'rep_code': '200'}, 'data': {'showDateCN': stamp}, 'records': [
        {'productCode': code, 'date': '26-11-10', 'weightedRate': weighted, 'latestRate': latest, 'avgPrd': term}
        for code, weighted, latest, term in rows]}, ensure_ascii=False).encode()


FULL = board('2026-11-10 9:30', [
    ('DR001', '1.3159', '1.3140', '1'),
    ('DR007', '1.3200', '1.3200', '7'),
    ('DR014', '1.3000', '1.3000', '14'),
])
PARTIAL = board('2026-11-10 9:15', [
    ('DR001', '1.3133', '1.3199', '1'),
    ('DR014', '1.3000', '1.3000', '14'),
])
# The daily file already contains a same-day print. It must not become the close curve.
CSV = ('2026-11-10,,,,,,1.3159,1.3200,1.3000\n'
       '2026-11-09,,,,,,1.31,1.358,1.3291\n'
       '2026-11-06,,,,,,1.2886,1.345,1.3295\n').encode()
POLICY = {'date': '2026-11-10', 'term_days': 1, 'rate_percent': '1.25',
          'bid_yi': '1900', 'awarded_yi': '1900'}
STALE_POLICY = {'date': '2026-11-09', 'term_days': 1, 'rate_percent': '1.25',
                'bid_yi': '1820', 'awarded_yi': '1820'}


def _snapshot_from(raw):
    quote = parse_quotes(raw, '2026-11-09')
    return {'quotes': quote, 'history': {'start': '2026-11-06', 'end': '2026-11-09', 'series': ['DR001', 'DR007', 'DR014'],
            'rows': [
                {'date': '2026-11-06', 'DR001': '1.2886', 'DR007': '1.345', 'DR014': '1.3295'},
                {'date': '2026-11-09', 'DR001': '1.31', 'DR007': '1.358', 'DR014': '1.3291'},
            ]}, 'central_bank': dict(POLICY)}


def test_accepted_intraday_copy_uses_the_same_day_operation_and_explicit_bases():
    copy = build_copy(_snapshot_from(FULL))
    assert copy['title'] == '【流动性指标】DR001高于隔夜逆回购操作利率6.59个基点'
    first, second = copy['body'].split('\n\n')
    assert '2026-11-10 9:30' in first and '盘中加权利率' in first and '不是当日收盘' in first
    assert 'DR001盘中加权利率为1.3159%' in first
    assert '较2026-11-09已发布日度加权利率1.31%上行0.59个基点' in first
    assert 'DR007盘中加权利率为1.3200%' in first
    assert '较2026-11-09已发布日度加权利率1.358%下行3.80个基点' in first
    assert 'DR001较当日隔夜逆回购操作利率1.25%高6.59个基点' in second
    assert '央行2026-11-10开展1900亿元隔夜逆回购，投标量1900亿元，中标量1900亿元' in second
    assert '1820' not in copy['body'] and '17:00' not in copy['body']
    assert '收盘加权利率' not in first
    assert '短期货币市场利率' not in copy['body']


def test_previous_day_operation_is_not_rewritten_as_today():
    snapshot = _snapshot_from(FULL)
    snapshot['central_bank'] = dict(STALE_POLICY)
    copy = build_copy(snapshot)
    assert '1820' not in copy['body'] and '2026-11-09开展' not in copy['body']
    assert '6.59' not in copy['title'] and '1900' not in copy['body']
    assert '1.3159' in copy['body'] and '0.59个基点' in copy['body']


def test_a_changed_board_changes_the_rates_not_only_the_date():
    early = _snapshot_from(PARTIAL)
    early['central_bank'] = None
    later = _snapshot_from(FULL)
    early_copy, later_copy = build_copy(early), build_copy(later)
    assert '1.3133' in early_copy['body'] and '上行0.33个基点' in early_copy['body']
    assert 'DR007本时刻未公布' in early_copy['body'] and '1.358' not in early_copy['body']
    assert '1.3159' in later_copy['body'] and '上行0.59个基点' in later_copy['body']
    assert '1.3200' in later_copy['body'] and '下行3.80个基点' in later_copy['body']
    assert early_copy['body'] != later_copy['body']


def test_fetch_keeps_the_live_board_and_leaves_today_off_the_daily_curve(tmp_path, monkeypatch):
    from app.briefs.engines.liquidity import fetch_data
    def read_source(name, *args, **kwargs):
        return (FULL if name.endswith('json') else CSV), {'url': name}
    monkeypatch.setattr(fetch_data, 'read_source', read_source)
    snapshot = fetch_snapshot(tmp_path, '2026-11-09')
    quotes = snapshot['quotes']
    assert quotes['updated_at'] == '2026-11-10 9:30'
    assert quotes['timestamp_kind'] == 'intraday'
    assert [row['weighted_rate'] for row in quotes['rows']] == ['1.3159', '1.3200', '1.3000']
    assert [row['latest_rate'] for row in quotes['rows']] == ['1.3140', '1.3200', '1.3000']
    assert quotes['pending_products'] == []
    assert snapshot['history']['end'] == '2026-11-09'
    assert all(row['date'] < '2026-11-10' for row in snapshot['history']['rows'])
    assert snapshot['history']['rows'][-1]['DR001'] == '1.31'
    assert snapshot['history']['rows'][-1]['DR007'] == '1.358'
    assert '17:00' not in quotes['updated_at']


def test_missing_dr007_does_not_backfill_the_previous_close(tmp_path, monkeypatch):
    from app.briefs.engines.liquidity import fetch_data
    monkeypatch.setattr(fetch_data, 'read_source', lambda name, *args, **kwargs: (
        (PARTIAL if name.endswith('json') else CSV), {'url': name}))
    snapshot = fetch_snapshot(tmp_path, '2026-11-09')
    quotes = snapshot['quotes']
    assert quotes['pending_products'] == ['DR007']
    assert [row['product'] for row in quotes['rows']] == ['DR001', 'DR014']
    assert quotes['rows'][0]['weighted_rate'] == '1.3133'
    assert quotes['rows'][0]['latest_rate'] == '1.3199'
    assert all(row.get('weighted_rate') != '1.358' for row in quotes['rows'])
    assert snapshot['history']['rows'][-1]['DR007'] == '1.358'


@pytest.mark.skipif(not hasattr(time, 'tzset'), reason='time.tzset is unavailable on Windows')
def test_source_stamp_is_not_shifted_by_the_server_zone():
    previous = os.environ.get('TZ')
    os.environ['TZ'] = 'America/New_York'
    time.tzset()
    try:
        parsed = parse_quotes(FULL, '2026-11-09')
    finally:
        if previous is None: os.environ.pop('TZ', None)
        else: os.environ['TZ'] = previous
        time.tzset()
    assert parsed['quote_date'] == '2026-11-10'
    assert parsed['updated_at'] == '2026-11-10 9:30'
    assert datetime.strptime(parsed['updated_at'], '%Y-%m-%d %H:%M').hour == 9


def test_poster_table_matches_the_copy_and_changes_when_the_rates_change(tmp_path):
    early, later = _snapshot_from(PARTIAL), _snapshot_from(FULL)
    early['central_bank'] = None
    _, _, early_audit = render_poster(early, tmp_path / 'early', logo_path=WATERMARK)
    _, later_svg, later_audit = render_poster(later, tmp_path / 'later', logo_path=WATERMARK)
    def cells(audit):
        return {(item['product'], item['field']): item['display'] for item in audit['table_cells']}
    early_cells, later_cells = cells(early_audit), cells(later_audit)
    assert early_cells[('DR001', 'weighted_rate')] == '1.3133'
    assert early_cells[('DR001', 'latest_rate')] == '1.3199'
    assert early_cells[('DR007', 'weighted_rate')] == '待公布'
    assert early_cells[('DR014', 'weighted_rate')] == '1.3000'
    assert later_cells[('DR001', 'weighted_rate')] == '1.3159'
    assert later_cells[('DR001', 'latest_rate')] == '1.3140'
    assert later_cells[('DR007', 'weighted_rate')] == '1.3200'
    assert later_cells[('DR007', 'latest_rate')] == '1.3200'
    assert later_cells[('DR014', 'latest_rate')] == '1.3000'
    assert '1.358' not in set(later_cells.values())
    text = later_svg.read_text(encoding='utf-8')
    assert '当日盘中成交行情' in text and '前一交易日收盘行情' not in text
    assert '2026年11月10日 9:30' in text and '2026年11月9日' in text and '不含当日盘中' in text
    assert '17:00' not in text


def test_same_day_announcement_is_selected(tmp_path, monkeypatch):
    from app.briefs import central_bank
    index = ('<a href="/zhengcehuobisi/125207/125213/125431/125475/2026110908582629771/index.html">公开市场业务交易公告</a>'
             '<a href="/zhengcehuobisi/125207/125213/125431/125475/2026111008535086262/index.html">公开市场业务交易公告</a>')
    pages = {
        '20261109': '<p>2026年11月9日全额满足</p><table><tr><td>隔夜</td><td>1.25%</td><td>1820亿元</td><td>1820亿元</td></tr></table>',
        '20261110': '<p>2026年11月10日全额满足</p><table><tr><td>隔夜</td><td>1.25%</td><td>1900亿元</td><td>1900亿元</td></tr></table>',
    }
    def download(url):
        for stamp, html in pages.items():
            if stamp in url: return html.encode()
        return index.encode()
    monkeypatch.setattr(central_bank, 'download', download)
    current = central_bank.fetch('2026-11-10', tmp_path)
    previous = central_bank.fetch('2026-11-09', tmp_path / 'older')
    assert current['date'] == '2026-11-10' and current['bid_yi'] == '1900' and current['awarded_yi'] == '1900'
    assert current['rate_percent'] == '1.25' and previous['awarded_yi'] == '1820'
    assert '20261110' in current['url'] and '20261109' not in current['url']


def test_adapter_asks_for_the_board_day_operation(tmp_path, monkeypatch):
    from app.briefs import sources
    from app.briefs.engines.liquidity import fetch_data
    snapshot = _snapshot_from(FULL)
    snapshot['history']['rows'] = [
        {'date': '2026-11-10', 'DR001': '9.9999', 'DR007': '9.9999', 'DR014': '9.9999'},
        *snapshot['history']['rows']]
    monkeypatch.setattr(fetch_data, 'fetch_snapshot', lambda out, day, **kwargs: snapshot)
    seen = []
    monkeypatch.setattr(sources.central_bank, 'fetch', lambda day, out: seen.append(day) or {
        'date': day, 'term_days': 1, 'rate_percent': '1.25', 'bid_yi': '1900', 'awarded_yi': '1900'})
    monkeypatch.setattr('app.briefs.engines.liquidity.render.render_poster',
                        lambda *args, **kwargs: (tmp_path / '图.png', None, {'table_cells': []}))
    (tmp_path / '图.png').write_bytes(b'png')
    result = sources.liquidity('2026-11-09', tmp_path / 'run', tmp_path / 'state')
    assert seen == ['2026-11-10']
    assert result['data_date'] == '2026-11-10'
    assert result['data_date_policy'] == 'intraday_board'
    assert result['facts']['history_range'][1] == '2026-11-09'
    assert '9.9999' not in result['dynamic_body']
    assert '1900' in result['dynamic_body'] and '1820' not in result['dynamic_body']
    assert Decimal('6.59') == (Decimal('1.3159') - Decimal('1.25')) * 100


def test_runner_accepts_today_board_and_rejects_an_older_substitute(tmp_path, monkeypatch):
    from app.briefs import runner
    def adapter(day, out, state, refresh=False, *, data_date):
        out.mkdir(parents=True, exist_ok=True)
        (out / '图.png').write_bytes(b'png')
        return {'title': '【流动性指标】DR001高于隔夜逆回购操作利率6.59个基点',
                'dynamic_body': 'DR001盘中加权利率为1.3159%，较2026-11-09已发布日度加权利率1.31%上行0.59个基点。',
                'images': ['图.png'], 'data_date': data_date, 'data_date_policy': 'intraday_board',
                'facts': {'quotes': {'timestamp_kind': 'intraday', 'quote_date': data_date,
                                      'rows': [{'product': 'DR001', 'weighted_rate': '1.3159'}]}},
                'warnings': []}
    monkeypatch.setitem(runner.ADAPTERS, 'liquidity', lambda *args, **kwargs: adapter(*args, data_date='2026-11-10'))
    result = runner.generate('liquidity', '2026-11-09', tmp_path, 'run-new')
    assert result['valid'] and result['data_date'] == '2026-11-10' and '1.3159' in result['dynamic_body']
    monkeypatch.setitem(runner.ADAPTERS, 'liquidity', lambda *args, **kwargs: adapter(*args, data_date='2026-11-08'))
    with pytest.raises(ValueError, match='来源数据日与目标日期不符'):
        runner.generate('liquidity', '2026-11-09', tmp_path / 'old', 'run-old')
