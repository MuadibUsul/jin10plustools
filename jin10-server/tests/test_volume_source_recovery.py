from datetime import date
from decimal import Decimal
import json

import pytest

from app.briefs import sources, volume_recovery
from app.briefs.failures import MissingObservations, SourceContractError
from app.briefs.trading_calendar import sessions

DAY = '2026-11-20'
PREVIOUS = {'date': '2026-11-19', 'turn': '13051.47267286', 'vol': '904.376952'}
# Numeric facts and the source's field labels, without unrelated article prose.
TEXT = ('11月20日，沪深两市全天成交额合计14222.01亿元。'
        '其中，沪市成交额为7025.74亿元（上一交易日为6369.09亿元），'
        '成交量为5.0亿手（上一交易日为4.13亿手）；'
        '深市成交额为7196.27亿元（上一交易日为6682.38亿元），'
        '成交量为5.72亿手（上一交易日为4.91亿手）。')


def mock_flash(monkeypatch, *, original=TEXT, empty=False):
    def fetch(url, folder, name, deadline):
        if url.startswith('https://flash-api.'):
            raw = json.dumps({'status': 200, 'data': [] if empty else [{
                'id': '20261120150127681800', 'time': '2026-11-20 15:01:27',
                'data': {'content': TEXT, 'exclusive_to': []}}]}, ensure_ascii=False).encode()
        else:
            raw = ('<meta name="description" content="' + original + '">').encode()
        (folder / name).write_bytes(raw)
        return raw
    monkeypatch.setattr(volume_recovery, '_read', fetch)


def test_published_precision_and_volume_conversion_are_explicit():
    point = volume_recovery.parse_closing(TEXT, DAY, PREVIOUS)
    assert point['turn'] == '14222.01'
    assert Decimal(point['vol']) == Decimal('1072')
    assert point['source_precision']['amount_yi'] == '0.01'
    assert point['source_precision']['sh_volume_yi_hands'] == '5.0'


@pytest.mark.parametrize('text,previous', [
    (TEXT.replace('14222.01', '14223.01'), PREVIOUS),
    (TEXT.replace('11月20日', '11月19日'), PREVIOUS),
    (TEXT.replace('亿手', '亿股'), PREVIOUS),
    (TEXT, {**PREVIOUS, 'date': '2026-11-18'}),
    (TEXT, {**PREVIOUS, 'turn': '13052'}),
    (TEXT, {**PREVIOUS, 'vol': '9.04'}),
])
def test_conflicting_amounts_dates_and_units_stop_recovery(text, previous):
    with pytest.raises(SourceContractError):
        volume_recovery.parse_closing(text, DAY, previous)


def test_two_original_source_paths_must_agree(tmp_path, monkeypatch):
    mock_flash(monkeypatch, original=TEXT.replace('14222.01', '14223.01'))
    stored = []
    with pytest.raises(SourceContractError):
        volume_recovery.recover_dates([DAY], [PREVIOUS], tmp_path, stored.append)
    assert not stored
    assert (tmp_path / 'volume-flash-2026-11-20-original.html').is_file()


def primary_rows():
    rows = [{'date': day, 'sh_turn': '7000', 'sz_turn': '6000',
             'sh_vol': '400', 'sz_vol': '400'}
            for day in sessions('xshg', '2026-09-01', '2026-11-23') if day != DAY]
    prior = next(row for row in rows if row['date'] == PREVIOUS['date'])
    prior.update(sh_turn='6369.09057689', sz_turn='6682.38209597',
                 sh_vol='412.912755', sz_vol='491.464197')
    return rows


def run_volume(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from app.briefs import charts
    @contextmanager
    def reply(*args, **kwargs):
        class Response:
            def read(self, *args):return json.dumps({'data': primary_rows()}).encode()
        yield Response()
    monkeypatch.setattr(sources, 'urlopen', reply)
    monkeypatch.setattr(sources, 'completed_session', lambda _: date(2026, 11, 23))
    monkeypatch.setattr(charts, 'volume_chart', lambda rows, out: out / 'chart.png')
    out = tmp_path / 'run';out.mkdir()
    state = tmp_path / 'state';state.mkdir()
    return out, state


def test_missing_day_is_recovered_and_ma20_recomputed_through_normal_adapter(tmp_path, monkeypatch):
    mock_flash(monkeypatch)
    out, state = run_volume(tmp_path, monkeypatch)
    result = sources.volume('2026-11-23', out, state)
    rows = sources.read(state / 'volume-history.json')['rows']
    recovered = next(row for row in rows if row['date'] == DAY)
    assert recovered['source_url'].endswith('20261120150127681800')
    assert recovered['source_sha256'] and recovered['source_precision']
    assert result['facts']['previous_date'] == DAY
    expected = sum(Decimal(row['turn']) for row in rows[-20:]) / 20
    assert Decimal(result['facts']['ma20_yi']) == expected
    assert result['facts']['supplemental_sources'][0]['date'] == DAY
    assert result['warnings']


def test_absent_flash_keeps_failure_and_never_fills_the_missing_row(tmp_path, monkeypatch):
    mock_flash(monkeypatch, empty=True)
    out, state = run_volume(tmp_path, monkeypatch)
    with pytest.raises(MissingObservations) as caught:
        sources.volume('2026-11-23', out, state)
    assert DAY in caught.value.missing_dates
    assert not any(row['date'] == DAY for row in sources.read(state / 'volume-history.json')['rows'])


def test_historical_unknown_moving_average_draws_a_gap(tmp_path):
    from app.briefs.charts import volume_chart
    rows = [{'date': '2026-11-19', 'turn': '13051.47', 'vol': '904.37', 'ma20': None},
            {'date': DAY, 'turn': '14222.01', 'vol': '1072', 'ma20': '15000'}]
    assert volume_chart(rows, tmp_path).is_file()


def test_preclose_row_and_missing_day_are_replaced_only_with_verified_closing_reports(tmp_path, monkeypatch):
    from contextlib import contextmanager
    out, state = run_volume(tmp_path, monkeypatch)
    rows = primary_rows()
    target = next(row for row in rows if row['date'] == '2026-11-23')
    target.update(sh_turn='4365.29537299', sz_turn='4798.41620833',
                  sh_vol='306.798672', sz_vol='364.815718',
                  updated_at='2026-11-23T03:59:01.000Z')
    @contextmanager
    def primary(*args, **kwargs):
        class Response:
            def read(self, *args):return json.dumps({'data': rows}).encode()
        yield Response()
    monkeypatch.setattr(sources, 'urlopen', primary)
    text23 = ('11月23日，沪深两市全天成交额合计15528.8亿元。'
              '其中，沪市成交额为8000亿元（上一交易日为7025.74亿元），'
              '成交量为5.12亿手（上一交易日为5.0亿手）；'
              '深市成交额为7528.8亿元（上一交易日为7196.27亿元），'
              '成交量为6.07亿手（上一交易日为5.72亿手）。')
    def read_flash(url, folder, name, deadline):
        day23 = '2026-11-23' in name
        content = text23 if day23 else TEXT
        if 'flash-api.' in url:
            data = {'status': 200, 'data': [{
                'id': '20261123150150092800' if day23 else '20261120150127681800',
                'time': '2026-11-23 15:01:50' if day23 else '2026-11-20 15:01:27',
                'data': {'content': content, 'exclusive_to': []}}]}
            raw = json.dumps(data, ensure_ascii=False).encode()
        else:
            raw = ('<meta name="description" content="' + content + '">').encode()
        (folder / name).write_bytes(raw)
        return raw
    monkeypatch.setattr(volume_recovery, '_read', read_flash)
    result = sources.volume('2026-11-23', out, state)
    assert result['facts']['amount_yi'] == '15528.8'
    assert result['facts']['change_yi'] == '1306.79'
    assert sources.read(out / 'volume-intraday-rejected.json')['dates'] == ['2026-11-23']
    stored = sources.read(state / 'volume-history.json')['rows']
    assert next(row for row in stored if row['date'] == '2026-11-23')['source_kind'] == 'jin10_published_closing_flash'
