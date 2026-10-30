"""Whole-category factual and isolation checks for the fixed-brief pilot."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.briefs import editorial_suite


DATA = json.loads((Path(__file__).parent / 'fixtures/editorial_suite_cases.json').read_text(encoding='utf-8'))
CASES = DATA['cases']
CATEGORIES = {'volume', 'turnover', 'valuation', 'liquidity', 'oil_etf',
              'token_cost', 'gpu_rental', 'us_barometer', 'us_structure'}


def normal_cases():
    return {category: next(case for case in CASES if case['category'] == category and case['expect']['status'] == 'ok')
            for category in CATEGORIES}


def preserve(payload):
    return {key: value for key, value in payload.items() if key != 'editorial'}


def test_every_current_fixed_category_has_multiple_independent_cases():
    assert DATA['provenance'] == 'synthetic_not_market_data'
    assert {case['category'] for case in CASES} == CATEGORIES
    assert len({case['id'] for case in CASES}) == len(CASES)
    assert all(sum(case['category'] == category for case in CASES) >= 5 for category in CATEGORIES)
    assert {case['split'] for case in CASES} == {'development', 'holdout'}


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['id'])
def test_all_category_fact_and_boundary_cases(case):
    source = deepcopy(case['payload'])
    original = deepcopy(source)
    if case['expect']['status'] == 'rejected':
        with pytest.raises(ValueError):
            editorial_suite.build_candidate(case['category'], source)
        assert source == original
        return
    candidate = editorial_suite.build_candidate(case['category'], source)
    assert source == original
    assert candidate['status'] == 'candidate'
    assert candidate['title'] and candidate['body']
    assert '\n' not in candidate['title']
    assert candidate['scenario'] and candidate['bindings']
    assert candidate['pack_version'] and candidate['pack_digest'] and candidate['facts_digest']
    assert candidate['hard_checks']['facts'] is True
    assert candidate['hard_checks']['structure'] is True
    assert set(candidate['plan']) == {'headline', 'lead', 'comparison', 'closing'}
    assert source['fixed_notes'] not in candidate['body']
    for phrase in ('结构性紧缺', '需求旺盛', '投资建议', '资金持续从小盘股中流出'):
        assert phrase not in candidate['title'] + candidate['body']


@pytest.mark.parametrize('category', sorted(CATEGORIES))
def test_shadow_is_additive_for_every_category(category):
    source = deepcopy(normal_cases()[category]['payload'])
    original = deepcopy(source)
    result = editorial_suite.apply_editorial(source, category, {}, mode='shadow')
    assert result is source and preserve(result) == original
    assert result['editorial']['status'] == 'shadow'
    assert result['editorial']['candidate']['status'] == 'candidate'


@pytest.mark.parametrize('category', sorted(CATEGORIES))
def test_off_preserves_the_existing_product(category):
    source = deepcopy(normal_cases()[category]['payload'])
    original = deepcopy(source)
    assert editorial_suite.apply_editorial(source, category, {}, mode='off') is source
    assert source == original


@pytest.mark.parametrize('category', sorted(CATEGORIES))
def test_unreviewed_packs_cannot_replace_copy(category):
    source = deepcopy(normal_cases()[category]['payload'])
    original = deepcopy(source)
    editorial_suite.apply_editorial(source, category, {}, mode='active')
    assert preserve(source) == original
    assert source['editorial']['status'] == 'shadow'
    assert source['editorial']['reason'] == 'pack_not_reviewed'


@pytest.mark.parametrize('category', sorted(CATEGORIES))
@pytest.mark.parametrize('setting', [{'enabled': False}, {'instructions': '团队既有规范'}])
def test_team_overrides_are_not_silently_replaced(category, setting):
    source = deepcopy(normal_cases()[category]['payload'])
    original = deepcopy(source)
    editorial_suite.apply_editorial(source, category, {'skills': {category: setting}}, mode='shadow')
    assert preserve(source) == original
    assert source['editorial']['status'] == 'skipped'


@pytest.mark.parametrize('category', sorted(CATEGORIES))
def test_custom_body_template_remains_authoritative(category):
    source = deepcopy(normal_cases()[category]['payload'])
    source['body_template_custom'] = True
    original = deepcopy(source)
    editorial_suite.apply_editorial(source, category, {}, mode='shadow')
    assert preserve(source) == original
    assert source['editorial']['status'] == 'skipped'


def test_unknown_category_is_not_wired_into_fixed_briefs():
    source = deepcopy(CASES[0]['payload'])
    original = deepcopy(source)
    assert editorial_suite.apply_editorial(source, 'fresh', {}) is source
    assert source == original
    with pytest.raises(ValueError, match='unsupported_category'):
        editorial_suite.build_candidate('fresh', source)


@pytest.mark.parametrize('category', sorted(CATEGORIES))
def test_model_cannot_write_arbitrary_prose_or_unknown_modules(category):
    source = deepcopy(normal_cases()[category]['payload'])
    good = editorial_suite.build_candidate(category, source)
    with pytest.raises(ValueError):
        editorial_suite.build_candidate(category, source, selection={**good['plan'], 'lead': '私自新增的判断'})


def test_default_suite_mode_is_shadow(monkeypatch):
    monkeypatch.setattr(editorial_suite, 'get_settings', lambda: SimpleNamespace(
        briefs_editorial_mode='shadow', briefs_gpu_editorial_mode='shadow'))
    source = deepcopy(normal_cases()['volume']['payload'])
    editorial_suite.apply_editorial(source, 'volume', {})
    assert source['editorial']['status'] == 'shadow'


def test_us_structure_keeps_three_index_changes_in_the_first_numbered_item():
    source = deepcopy(normal_cases()['us_structure']['payload'])
    body = editorial_suite.build_candidate('us_structure', source)['body']
    first_item = body.split('1、', 1)[1].split('\n', 1)[0]
    assert first_item.index('纳斯达克') < first_item.index('标普500') < first_item.index('道琼斯')
    assert '1.88%' in first_item and '1.12%' in first_item and '0.09%' in first_item


def test_original_gpu_mode_remains_usable_through_suite(monkeypatch):
    monkeypatch.setattr(editorial_suite, 'get_settings', lambda: SimpleNamespace(
        briefs_editorial_mode='active', briefs_gpu_editorial_mode='off'))
    source = deepcopy(normal_cases()['gpu_rental']['payload'])
    original = deepcopy(source)
    editorial_suite.apply_editorial(source, 'gpu_rental', {})
    assert source == original
