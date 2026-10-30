"""Financial semantics and release isolation beyond happy-path suite examples."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.briefs import editorial_suite, gpu_editorial_ai


CASES = json.loads((Path(__file__).parent / 'fixtures/editorial_suite_cases.json').read_text(encoding='utf-8'))['cases']


def source(category):
    return deepcopy(next(row['payload'] for row in CASES
                         if row['category'] == category and row['expect']['status'] == 'ok'))


def test_liquidity_uses_both_close_rates_and_structured_prior_evidence():
    payload = source('liquidity')
    payload['facts']['previous_close'] = {
        'DR001': {'date': '2026-10-29', 'weighted_rate': '1.3000'},
        'DR007': {'date': '2026-10-29', 'weighted_rate': '1.4000'},
    }
    copy = editorial_suite.build_candidate('liquidity', payload)
    assert 'DR001收盘加权利率为1.2533%' in copy['body']
    assert 'DR007收盘加权利率为1.3352%' in copy['body']
    assert '下行4.67个基点' in copy['body'] and '下行6.48个基点' in copy['body']
    assert '2026-10-29' in copy['body']
    # A different old change is not silently retained or relabelled.
    payload['dynamic_body'] += 'DR007较2026-10-29下行99.99个基点。'
    with pytest.raises(ValueError, match='unbound_rule_numbers'):
        editorial_suite.build_candidate('liquidity', payload)


@pytest.mark.parametrize('stamp', ['2026-10-30 16:59', '2026-10-30 18:00', '2026-10-29 17:00'])
def test_liquidity_refuses_a_non_target_close(stamp):
    payload = source('liquidity')
    payload['facts']['quotes']['updated_at'] = stamp
    with pytest.raises(ValueError):
        editorial_suite.build_candidate('liquidity', payload)


def test_liquidity_source_adds_prior_rates_without_changing_the_old_rule_body(tmp_path, monkeypatch):
    from app.briefs import sources
    from app.briefs.engines.liquidity import render, copy_tracks
    payload = source('liquidity')
    snapshot = {**payload['facts'], 'history': {
        'start': '2026-07-30', 'end': '2026-10-30', 'rows': [
            {'date': '2026-10-29', 'DR001': '1.3000', 'DR007': '1.4000'},
            {'date': '2026-10-30', 'DR001': '1.2533', 'DR007': '1.3352'},
        ]}}
    monkeypatch.setattr(render, 'render_poster', lambda *args, **kwargs: (tmp_path / 'chart.png', None, {}))
    monkeypatch.setattr(copy_tracks, 'build_copy', lambda *args: {'body': '旧规则稿。'})
    snapshot['central_bank'] = None
    result = sources.render_liquidity(snapshot, tmp_path)
    assert result['dynamic_body'] == '旧规则稿。'
    assert result['facts']['previous_close']['DR007'] == {
        'date': '2026-10-29', 'weighted_rate': '1.4000'}


def test_oil_keeps_total_exposure_and_futures_as_different_metrics():
    copy = editorial_suite.build_candidate('oil_etf', source('oil_etf'))
    assert '名义敞口估算约1883.40万桶' in copy['body']
    assert '其中期货持仓1583.60万桶' in copy['body']
    assert copy['bindings']['USO']['metric'] == '名义敞口估算'
    assert 'Brent（BNO）期货持仓约696.30万桶' in copy['body']
    from app.briefs.delivery import complete_body, OIL_CLOSING
    delivered = complete_body('oil_etf', copy['body'])
    assert delivered.endswith(copy['body'] + OIL_CLOSING)
    assert '\n' + OIL_CLOSING not in delivered


def test_oil_does_not_relabel_unverified_total_changes_as_futures_changes():
    payload = source('oil_etf')
    payload['facts']['funds'][0].update(change_barrels='200000', change_basis='total_exposure')
    with pytest.raises(ValueError, match='unverified_oil_change_basis'):
        editorial_suite.build_candidate('oil_etf', payload)


def test_oil_total_must_cover_the_futures_component():
    payload = source('oil_etf')
    payload['facts']['funds'][0]['futures_barrels'] = '20000000'
    with pytest.raises(ValueError, match='inconsistent_oil_metric'):
        editorial_suite.build_candidate('oil_etf', payload)


def test_out_of_range_us_erp_does_not_invent_a_new_original_grade():
    payload = source('us_barometer')
    payload['facts']['erp_pct'] = '-1.20'
    original = deepcopy(payload)
    editorial_suite.apply_editorial(payload, 'us_barometer', {}, mode='shadow')
    assert payload['editorial']['status'] == 'fallback'
    assert {k: v for k, v in payload.items() if k != 'editorial'} == original


def test_calculated_token_week_change_must_agree_with_its_baseline():
    payload = source('token_cost')
    payload['facts']['weekly_change_pct'] = '25.00'
    with pytest.raises(ValueError, match='invalid_change'):
        editorial_suite.build_candidate('token_cost', payload)


def test_a_new_fact_revision_has_its_own_candidate_digest():
    payload = source('turnover')
    previous = editorial_suite.build_candidate('turnover', payload)
    payload['facts']['observation_revision'] = 2
    revised = editorial_suite.build_candidate('turnover', payload)
    assert previous['body'] == revised['body']
    assert previous['facts_digest'] != revised['facts_digest']


@pytest.mark.parametrize('category,field', [('token_cost', 'drawdown_pct'), ('gpu_rental', 'b200_gap_pct')])
def test_peak_distance_must_match_the_two_bound_prices(category, field):
    payload = source(category)
    payload['facts'][field] = '30.00'
    with pytest.raises(ValueError, match='peak_mismatch'):
        editorial_suite.build_candidate(category, payload)


def test_a_large_drop_from_the_peak_is_not_evidence_of_an_all_record_low():
    copy = editorial_suite.build_candidate('token_cost', source('token_cost'))
    assert '已收录低位' not in copy['title']
    assert '此前高点' in copy['title']


@pytest.mark.parametrize('current,previous,forbidden', [
    (('60.00', '8.00'), ('60.00', '9.00'), '低换手占比上升'),
    (('61.00', '9.00'), ('60.00', '9.00'), '高换手占比下降'),
    (('60.00', '9.00'), ('61.00', '9.00'), '高换手占比上升'),
])
def test_turnover_flat_leg_is_not_claimed_to_move(current, previous, forbidden):
    from app.briefs.engines.turnover.copy_tracks import build_facts, generate_rule_copy
    facts = build_facts({'date': '2026-10-30', 'low_ma9': current[0], 'high_ma9': current[1]},
                        {'date': '2026-10-29', 'low_ma9': previous[0], 'high_ma9': previous[1]})
    rule = generate_rule_copy(facts)
    payload = {'data_date': facts['date'], 'facts': facts, 'dynamic_body': rule['body']}
    copy = editorial_suite.build_candidate('turnover', payload)
    assert forbidden not in copy['title']


def test_missing_facts_cannot_be_filled_from_historical_examples():
    payload = source('volume')
    payload['facts'].pop('change_pct')
    original = deepcopy(payload)
    editorial_suite.apply_editorial(payload, 'volume', {}, mode='shadow')
    assert payload['editorial']['status'] == 'fallback'
    assert {key: value for key, value in payload.items() if key != 'editorial'} == original


@pytest.mark.parametrize('category', sorted(editorial_suite.SUPPORTED_CATEGORIES - {'gpu_rental'}))
def test_reviewed_active_uses_a_bounded_plan_and_only_replaces_text(category, monkeypatch):
    pack = editorial_suite.load_pack(category)
    pack.update(status='approved', review={'approved_by': 'synthetic-test-editor', 'reviewed_at': '2026-10-30'})
    monkeypatch.setattr(editorial_suite, 'load_pack', lambda requested: deepcopy(pack))
    monkeypatch.setattr(gpu_editorial_ai, 'select_plan', lambda settings, context: {
        'status': 'ok', 'model': 'test-model',
        'plan': {role: rows[-1]['id'] for role, rows in context['options'].items()}})
    payload = source(category)
    original = deepcopy(payload)
    editorial_suite.apply_editorial(payload, category, {}, mode='active')
    assert payload['editorial']['status'] == 'active'
    assert payload['editorial']['selection_status'] == 'model_selected'
    assert {k: v for k, v in payload.items() if k not in ('title', 'dynamic_body', 'editorial')} == {
        k: v for k, v in original.items() if k not in ('title', 'dynamic_body')}


def test_suite_off_is_a_kill_switch_even_if_gpu_override_is_active(monkeypatch):
    monkeypatch.setattr(editorial_suite, 'get_settings', lambda: SimpleNamespace(
        briefs_editorial_mode='off', briefs_gpu_editorial_mode='active'))
    payload = source('gpu_rental')
    original = deepcopy(payload)
    editorial_suite.apply_editorial(payload, 'gpu_rental', {})
    assert payload == original


def test_malformed_pack_cannot_break_a_legacy_product(monkeypatch):
    monkeypatch.setattr(editorial_suite, 'load_pack', lambda *args: {'schema_version': 1})
    payload = source('volume')
    original = deepcopy(payload)
    editorial_suite.apply_editorial(payload, 'volume', {}, mode='active')
    assert payload['editorial']['status'] == 'fallback'
    assert {k: v for k, v in payload.items() if k != 'editorial'} == original
