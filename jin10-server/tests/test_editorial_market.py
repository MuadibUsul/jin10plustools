"""Category fact boundaries for the three market editorial adapters."""
from copy import deepcopy

import pytest

from app.briefs.editorial_market import build_context


def token(**changes):
    facts = {'token_price': '0.9750', 'weekly_change_pct': '-1.82',
             'previous_date': '2026-10-07', 'previous_price': '0.9931',
             'range_low': '0.9750', 'range_high': '0.9931', 'range_count': 2,
             'tail_narrowed': False, 'prior_peak_price': '2.0651',
             'prior_peak_date': '2026-05-28', 'peak_relation': 'below',
             'drawdown_pct': '52.79', 'weekly_source': 'calculated', 'source_precision': 4}
    facts.update(changes)
    return {'data_date': '2026-10-14', 'facts': facts}


def test_token_preserves_precision_and_seven_day_comparison_without_mutation():
    payload = token()
    original = deepcopy(payload)
    result = build_context('token_cost', payload)
    assert payload == original
    assert '0.9750美元/百万Token' in result['slots']['primary']
    assert '10月7日' in result['slots']['primary'] and '下跌1.82%' in result['slots']['primary']
    assert '52.79%' in result['slots']['peak']
    assert result['slots']['range'] == ''  # Two observations do not establish a full interval pattern.


def test_public_token_preserves_two_places_and_reports_card_provenance():
    result = build_context('token_cost', token(token_price='0.96', source_precision=2,
        weekly_change_pct='-4.1', drawdown_pct='53.51', weekly_source='public_visible_summary', previous_date='',
        previous_price='', range_low='', range_high='', range_count=0))
    assert '0.96美元' in result['slots']['primary'] and '0.9600' not in result['slots']['primary']
    assert '官网展示值' in result['slots']['primary']


@pytest.mark.parametrize('changes,reason', [
    ({'token_price': 'NaN'}, 'invalid_price'),
    ({'token_price': '-1.0'}, 'invalid_price'),
    ({'previous_date': '2026-10-08'}, 'invalid_baseline'),
    ({'weekly_change_pct': '-100'}, 'invalid_change'),
    ({'peak_relation': 'tie'}, 'peak_mismatch'),
    ({'source_precision': 2}, 'invalid_precision'),
    ({'range_low': '1.5'}, 'invalid_range'),
])
def test_token_rejects_invalid_or_incompatible_facts(changes, reason):
    with pytest.raises(ValueError, match=reason):
        build_context('token_cost', token(**changes))


def test_token_missing_week_is_not_replaced_with_a_recent_price():
    result = build_context('token_cost', token(weekly_change_pct='', previous_date='', previous_price=''))
    assert result['scenario'] == 'missing_week'
    assert '基准缺失' in result['slots']['primary']


def barometer(**changes):
    facts = {'buffett_pct': '235.18', 'buffett_grade': '严重高估', 'erp_pct': '5.33',
             'erp_grade': '偏低', 'verdict': '较低', 'score': -3,
             'gdp_period': '2026-04-01', 'gdp_value': '32563.03'}
    facts.update(changes)
    return {'data_date': '2026-10-14', 'facts': facts}


def test_us_barometer_binds_both_grade_values_without_trading_advice():
    result = build_context('us_barometer', barometer())
    text = ''.join(result['slots'].values())
    assert '235.18%' in text and '5.33%' in text
    assert '性价比分级为较低' in text
    assert '买入机会' not in text and '投资建议' not in text
    assert result['scenario'] == 'relatively_low'


@pytest.mark.parametrize('changes', [{'score': 2}, {'erp_grade': '很高'}, {'verdict': '高'}])
def test_us_barometer_rejects_grade_mismatch(changes):
    with pytest.raises(ValueError, match='grade_mismatch'):
        build_context('us_barometer', barometer(**changes))


def structure_payload():
    from app.briefs.engines.us_structure.calculate import structure
    from tests.test_us_structure import fixtures
    result = structure('2026-10-14', fixtures())
    return {'data_date': '2026-10-14', 'title': result['title'],
            'dynamic_body': result['body'], 'facts': result['facts']}


def test_us_structure_additive_evidence_keeps_legacy_copy_and_fact_boundaries():
    payload = structure_payload()
    original = deepcopy(payload)
    result = build_context('us_structure', payload)
    assert payload == original
    assert '表明资金持续从小盘股中流出' in payload['dynamic_body']
    assert result['slots']['indices'].startswith('前一个交易日纳斯达克指数收跌1.88%')
    assert '标普500指数收跌1.12%' in result['slots']['indices']
    assert '道琼斯指数收跌0.09%' in result['slots']['indices']
    text = ''.join(result['slots'].values())
    assert '回撤约11%' in text and '4月16日以来低位' in text
    assert '较前一交易日回升' in text
    assert '资金' not in text
    assert '873.01' not in text  # Absolute gap was deliberately excluded from publication.


@pytest.mark.parametrize('field,value,reason', [
    ('gap_percentile', '1.01', 'invalid_breadth'),
    ('rut_drawdown', '1.00', 'invalid_drawdown'),
    ('ndx_change', 'Infinity', 'invalid_change'),
    ('gap_change', '50.00', 'invalid_breadth'),
    ('rut_low_since_month', 5, 'invalid_low_since'),
])
def test_us_structure_rejects_invalid_structural_evidence(field, value, reason):
    payload = structure_payload()
    payload['facts'][field] = value
    with pytest.raises(ValueError, match=reason):
        build_context('us_structure', payload)
