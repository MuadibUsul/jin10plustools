"""Daily ChinaBond checks must catch revisions to the earlier completed session."""
from __future__ import annotations

import json
from pathlib import Path

from app.briefs.engines.valuation import run_erp as erp


def _bond(day, value):
    return {'date': day, 'yield_source_text': value, 'yield_percent_number': float(value),
            'source_decimal_places': len(value.partition('.')[2]), 'tenor_years': 10,
            'curve_name': erp.CURVE_NAME, 'method_id': erp.BOND_METHOD_ID}


def test_recent_bond_merge_corrects_an_earlier_session_revision():
    bonds = {'2026-10-13': _bond('2026-10-13', '1.6654'),
             '2026-10-14': _bond('2026-10-14', '1.6516')}
    recent = [_bond('2026-10-13', '1.6655'), _bond('2026-10-14', '1.6516')]
    report = erp._merge_recent_bonds(bonds, recent, ('2026-10-13', '2026-10-14'))
    assert report['checked_dates'] == ['2026-10-13', '2026-10-14']
    assert report['corrections'] == [{
        'date': '2026-10-13', 'old_yield': '1.6654', 'new_yield': '1.6655',
        'old_value': '1.6654', 'new_value': '1.6655', 'changed': True,
    }]
    assert report['unchanged_dates'] == ['2026-10-14']
    assert bonds['2026-10-13']['yield_source_text'] == '1.6655'


def test_formatting_only_bond_change_is_not_a_revision():
    bonds = {'2026-10-14': _bond('2026-10-14', '1.6500')}
    report = erp._merge_recent_bonds(bonds, [_bond('2026-10-14', '1.65')], ('2026-10-14',))
    assert report['corrections'] == [] and report['unchanged_dates'] == ['2026-10-14']
    assert bonds['2026-10-14']['yield_source_text'] == '1.65'


def test_missing_earlier_bond_session_is_added_and_reported():
    bonds = {}
    report = erp._merge_recent_bonds(bonds, [_bond('2026-10-14', '1.6516')], ('2026-10-14',))
    assert report['added_dates'] == ['2026-10-14'] and report['corrections'] == []
    assert bonds['2026-10-14']['yield_source_text'] == '1.6516'


def test_run_erp_rechecks_latest_two_bonds_even_when_expected_is_already_stored(tmp_path, monkeypatch):
    history = tmp_path / 'history.json'
    history.write_text(json.dumps([
        {'date': '2026-10-13', 'close': '4000', 'pe_ttm_mcw': '12', 'bond_yield_number': '1.6655'},
        {'date': '2026-10-14', 'close': '4010', 'pe_ttm_mcw': '12.1', 'bond_yield_number': '1.6516'},
    ]), encoding='utf-8')
    bonds_path = tmp_path / 'bonds.json'
    bonds_path.write_text(json.dumps([
        _bond('2026-10-13', '1.6655'), _bond('2026-10-14', '1.6516'), _bond('2026-10-15', '1.6400'),
    ]), encoding='utf-8')
    calls = []

    def fake_close(expected, previous, output):
        return {'source': 'fixture-close', 'rows': [
            {'date': previous, 'close': '4010'}, {'date': expected, 'close': '4020'}]}

    def fake_pe(expected, previous, output, **kwargs):
        return {'source': 'fixture-pe', 'rows': [
            {'date': previous, 'pe_ttm_mcw_raw': '12.10', 'source': 'fixture-pe'},
            {'date': expected, 'pe_ttm_mcw_raw': '12.20', 'source': 'fixture-pe'}]}

    def fake_bond(dates, output, **kwargs):
        calls.append(list(dates))
        assert list(dates) == ['2026-10-14', '2026-10-15']
        return {'source': 'fixture-bond', 'normalized': [
            _bond('2026-10-14', '1.6517'), _bond('2026-10-15', '1.6400')]}

    def fake_render(rows, output, **kwargs):
        path = Path(output) / 'chart.png'
        path.write_bytes(b'chart')
        return path, {'rows': len(rows)}

    monkeypatch.setattr(erp, 'fetch_close', fake_close)
    monkeypatch.setattr(erp, 'fetch_pe', fake_pe)
    monkeypatch.setattr(erp, 'fetch_bond', fake_bond)
    monkeypatch.setattr(erp, '_render', fake_render)
    result = erp.run_erp(history, tmp_path / 'out', mode='free-official', expected_date='2026-10-15',
                         bond_history=bonds_path, force_refresh=True)
    assert calls == [['2026-10-14', '2026-10-15']]
    assert result['bond_revision_check']['corrections'][0]['date'] == '2026-10-14'
    assert result['bond_revision_check']['corrections'][0]['new_yield'] == '1.6517'
    snapshot = json.loads((tmp_path / 'out' / '数据快照.json').read_text(encoding='utf-8'))
    assert next(row for row in snapshot if row['date'] == '2026-10-14')['bond_yield_number'] == '1.6517'
    assert (tmp_path / 'out' / 'raw' / 'bond-update' / 'revision-check.json').is_file()
