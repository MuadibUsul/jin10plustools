"""The optional model selects bounded plans; it never authors financial prose."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from app.briefs import gpu_editorial, gpu_editorial_ai, runner


def payload():
    cases = json.loads((Path(__file__).parent / 'fixtures/gpu_editorial_cases.json').read_text(encoding='utf-8'))
    return deepcopy(cases['cases'][0]['payload'])


def context():
    return gpu_editorial.selection_context(payload(), gpu_editorial.load_pack())


def valid_plan(value):
    return {role: rows[-1]['id'] for role, rows in value['options'].items()}


def configured(monkeypatch):
    monkeypatch.setattr(gpu_editorial_ai, 'get_settings', lambda: SimpleNamespace(briefs_skill_ai_enabled=True))
    monkeypatch.setattr(gpu_editorial_ai, 'briefs_ai', lambda team: {
        'key': 'fake-test-key', 'model': 'effective-model', 'requested_model': 'requested-model',
        'provider': 'chat2api', 'base_url': 'https://example.test/v1'})


class Response:
    def __init__(self, plan):
        self.plan = plan

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        return json.dumps({'choices': [{'finish_reason': 'stop',
                           'message': {'content': json.dumps(self.plan)}}]}).encode()


def test_selector_repairs_invalid_ids_once_and_records_effective_model(monkeypatch):
    configured(monkeypatch)
    value = context()
    selected = valid_plan(value)
    requests = []

    def request(req, timeout):
        requests.append(json.loads(req.data))
        return Response({'title': '不可执行的自由改写'}) if len(requests) == 1 else Response(selected)

    monkeypatch.setattr(gpu_editorial_ai, 'urlopen', request)
    result = gpu_editorial_ai.select_plan({}, value)
    assert result['status'] == 'ok' and result['plan'] == selected
    assert result['attempts'] == len(requests) == 2
    assert result['model'] == 'effective-model'
    assert all(req['model'] == 'effective-model' for req in requests)
    assert 'fake-test-key' not in json.dumps(result)


def test_selector_cannot_deliver_free_prose_after_two_invalid_answers(monkeypatch):
    configured(monkeypatch)
    requests = []

    def request(req, timeout):
        requests.append(req)
        return Response({'headline': '任意改写', 'lead': '6.99', 'comparison': 'x', 'closing': 'x'})

    monkeypatch.setattr(gpu_editorial_ai, 'urlopen', request)
    result = gpu_editorial_ai.select_plan({}, context())
    assert result['status'] == 'failed' and result['reason'] == 'invalid_plan'
    assert len(requests) == 2 and 'plan' not in result


def test_provider_failure_is_not_retried_and_never_leaks_credentials(monkeypatch):
    configured(monkeypatch)

    def quota(req, timeout):
        raise HTTPError(req.full_url, 429, 'not exposed', {}, None)

    monkeypatch.setattr(gpu_editorial_ai, 'urlopen', quota)
    result = gpu_editorial_ai.select_plan({}, context())
    assert result['status'] == 'failed' and result['http_status'] == 429
    assert result['attempts'] == 1 and 'fake-test-key' not in json.dumps(result)


def test_disabled_selector_does_not_access_provider_or_network(monkeypatch):
    monkeypatch.setattr(gpu_editorial_ai, 'get_settings', lambda: SimpleNamespace(briefs_skill_ai_enabled=False))
    monkeypatch.setattr(gpu_editorial_ai, 'briefs_ai', lambda *args: pytest.fail('provider must not be read'))
    assert gpu_editorial_ai.select_plan({}, context())['status'] == 'disabled'


def reviewed_pack():
    pack = gpu_editorial.load_pack()
    pack['status'] = 'approved'
    pack['review'] = {'approved_by': 'synthetic-test-reviewer', 'reviewed_at': '2026-10-29'}
    return pack


def test_reviewed_active_selection_changes_only_copy_and_keeps_trace(monkeypatch):
    pack = reviewed_pack()
    monkeypatch.setattr(gpu_editorial, 'load_pack', lambda: pack)
    selected = valid_plan(gpu_editorial.selection_context(payload(), pack))
    monkeypatch.setattr(gpu_editorial_ai, 'select_plan', lambda *args: {
        'status': 'ok', 'plan': selected, 'model': 'test-model'})
    original = payload()
    result = gpu_editorial.apply_gpu_editorial(deepcopy(original), {}, mode='active')
    assert result['editorial']['status'] == 'active'
    assert result['editorial']['selection_status'] == 'model_selected'
    assert result['editorial']['candidate']['plan'] == selected
    assert {k: v for k, v in result.items() if k not in {'title', 'dynamic_body', 'editorial'}} == {
        k: v for k, v in original.items() if k not in {'title', 'dynamic_body'}}
    assert result['dynamic_body'] == result['editorial']['candidate']['body']
    assert result['editorial']['baseline']['body'] == original['dynamic_body']


def test_reviewed_active_provider_outage_uses_complete_deterministic_copy(monkeypatch):
    pack = reviewed_pack()
    monkeypatch.setattr(gpu_editorial, 'load_pack', lambda: pack)
    monkeypatch.setattr(gpu_editorial_ai, 'select_plan', lambda *args: {
        'status': 'failed', 'reason': 'network_error'})
    result = gpu_editorial.apply_gpu_editorial(payload(), {}, mode='active')
    assert result['editorial']['selection_status'] == 'deterministic_fallback'
    assert all(card in result['dynamic_body'] for card in ('B200', 'H100', 'A100'))


def test_shadow_and_unreviewed_active_never_call_a_new_model(monkeypatch):
    monkeypatch.setattr(gpu_editorial_ai, 'select_plan', lambda *args: pytest.fail('no new model in shadow'))
    for mode in ('shadow', 'active'):
        result = gpu_editorial.apply_gpu_editorial(payload(), {}, mode=mode)
        assert result['editorial']['status'] == 'shadow'


def test_a_model_cannot_select_a_title_from_another_scenario():
    source = payload()
    candidate = gpu_editorial.build_candidate(source)
    with pytest.raises(ValueError, match='invalid_plan'):
        gpu_editorial.build_candidate(source, selection={**candidate['plan'], 'headline': 'tie_direct'})


def test_every_eligible_module_combination_preserves_complete_fact_spans():
    source = payload()
    options = context()['options']
    for choices in product(*options.values()):
        plan = {role: row['id'] for role, row in zip(options, choices)}
        candidate = gpu_editorial.build_candidate(source, selection=plan)
        assert candidate['module_snapshot']['lead']['id'] == plan['lead']
        for key in ('b200', 'h100', 'a100'):
            assert candidate['bindings'][key]['price'] == source['facts'][key]
            assert f'{source["facts"][key]}美元/GPU每小时' in candidate['body']


def test_conflicting_week_source_metadata_is_rejected():
    source = payload()
    source['facts']['weekly_source'] = 'public_visible_history_mixed'
    source['facts']['week_change_sources'] = dict(a100='visible_history', h100='card', b200='card')
    source['process_details'] = {'sources': [{'label': 'A100 · 2026-10-29 · 公开卡片'}]}
    with pytest.raises(ValueError, match='source_provenance_missing'):
        gpu_editorial.build_candidate(source)


def test_malformed_team_setting_cannot_break_a_valid_legacy_product():
    source = payload()
    original = deepcopy(source)
    result = gpu_editorial.apply_gpu_editorial(source, {'skills': ['malformed']}, mode='shadow')
    assert result['editorial']['status'] == 'fallback'
    assert {key: value for key, value in result.items() if key != 'editorial'} == original


def test_style_pack_content_changes_cache_signature(tmp_path, monkeypatch):
    from app.briefs import editorial_suite
    path = tmp_path / 'style-pack.json'
    path.write_text('{"version":"one"}', encoding='utf-8')
    original_path = editorial_suite.pack_path
    monkeypatch.setattr(editorial_suite, 'pack_path',
                        lambda category: path if category == 'gpu_rental' else original_path(category))
    runner.pipeline_signature.cache_clear()
    try:
        before = runner.pipeline_signature()
        path.write_text('{"version":"two"}', encoding='utf-8')
        runner.pipeline_signature.cache_clear()
        assert runner.pipeline_signature() != before
    finally:
        runner.pipeline_signature.cache_clear()
