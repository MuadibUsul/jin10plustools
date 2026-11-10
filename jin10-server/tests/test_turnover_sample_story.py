from copy import deepcopy
from io import BytesIO
import json
import re
from types import SimpleNamespace

import pytest

from app.briefs import turnover_draft_ai
from app.briefs.editorial_authorization import adoption
from app.briefs.engines.turnover.copy_tracks import build_facts, generate_rule_copy
from app.briefs.skill_production import apply_skill_copy


def turnover_payload(current, previous):
    def row(values):
        day, low, high = values
        return {'date': day, 'low_ma9': low, 'high_ma9': high}

    facts = build_facts(row(current), row(previous))
    copy = generate_rule_copy(facts)
    return {'title': copy['title'], 'dynamic_body': copy['body'], 'data_date': current[0],
            'facts': facts, 'fixed_notes': '原图固定说明', 'images': ['existing-chart.png'],
            'warnings': [], 'valid': True}


def run_story(payload):
    source = deepcopy(payload)
    result = apply_skill_copy(payload, 'turnover', {'brief_editorial': adoption('test-operator')})
    assert result['editorial']['writer'] == 'sample_story_v2'
    assert result['editorial']['status'] == 'active'
    assert result['facts'] == source['facts']
    assert result['fixed_notes'] == source['fixed_notes']
    assert result['images'] == source['images']
    return result


@pytest.mark.parametrize('model_available', [True, False])
def test_turnover_recent_production_days_use_distinct_verified_angles(monkeypatch, model_available):
    drafts = {
        '2026-11-05': {'title': '{{low_title}}', 'body':
                       '{{date}}，{{low_turnover}}。{{high_turnover}}，{{reference_line}}。'},
        '2026-11-06': {'title': '{{reference_title}}', 'body':
                       '{{date}}，{{high_turnover}}，{{reference_line}}。\n\n同期，{{low_turnover}}。'},
        '2026-11-09': {'title': '{{high_title}}', 'body':
                       '{{date}}，{{high_turnover}}。{{reference_line}}。与此同时，{{low_turnover}}。'},
    }

    def request(_settings, context):
        if not model_available:
            return {'status': 'failed', 'reason': 'quota', 'http_status': 402}
        assert set(context['required_body_tokens']) == {'date', 'low_turnover', 'high_turnover', 'reference_line'}
        return {'status': 'ok', 'draft': drafts[context['data_date']]}

    monkeypatch.setattr(turnover_draft_ai, 'request_draft', request)
    observations = [
        ('2026-11-04', '65.79', '16.81'),
        ('2026-11-05', '66.29', '16.72'),
        ('2026-11-06', '66.24', '16.76'),
        ('2026-11-09', '66.37', '16.71'),
    ]
    expected = [
        ('0.50', '0.09', '0.28', '扩大|拉大|远离'),
        ('0.05', '0.04', '0.24', '收窄|缩小|靠近'),
        ('0.13', '0.05', '0.29', '扩大|拉大|远离'),
    ]
    titles = []
    for previous, current, changes in zip(observations, observations[1:], expected):
        result = run_story(turnover_payload(current, previous))
        editorial = result['editorial']
        assert editorial['selection_status'] == ('model_selected' if model_available else 'deterministic_fallback')
        candidate = editorial['candidate']
        assert candidate['draft_method'] == ('bound_prose' if model_available else 'facts_fallback')
        if model_available:
            assert candidate['plan'] is None and candidate['draft_template'] == drafts[current[0]]
            assert ('\n\n' in result['dynamic_body']) == (current[0] == '2026-11-06')
        else:
            assert 'draft_template' not in candidate
        bank = {row['id']: row for row in candidate['fact_bank_snapshot']}
        assert bank['low_turnover']['required'] and bank['high_turnover']['required']
        low_delta, high_delta, gap, direction = changes
        body = result['dynamic_body']
        assert all(f'{value}%' in body for value in current[1:])
        assert all(f'{value}个百分点' in body for value in (low_delta, high_delta, gap))
        assert '17%' in body and re.search(direction, body)
        titles.append(result['title'])

    assert len(set(titles)) == 3
    assert len({re.sub(r'\d+(?:\.\d+)?', '#', title) for title in titles}) > 1


@pytest.mark.parametrize('previous_high,high,low,direction,crossing', [
    ('16.96', '17.02', '66.10', 'warming', '突破|升破|上穿|升至|重返|站上'),
    ('17.02', '16.96', '66.45', 'cooling', '跌破|降破|下穿|降至|跌至'),
])
def test_turnover_verified_crossing_precedes_streak_and_model_cannot_drop_a_leg(
        monkeypatch, previous_high, high, low, direction, crossing):
    monkeypatch.setattr(turnover_draft_ai, 'request_draft', lambda *_: {'status': 'ok', 'draft': {
        'title': '{{reference_title}}', 'body': '{{date}}，{{high_turnover}}。{{reference_line}}。'}})
    payload = turnover_payload(('2026-11-10', low, high), ('2026-11-09', '66.37', previous_high))
    payload['facts']['editorial_features'] = {
        'trend_direction': direction, 'trend_streak': 2, 'comparison_start': '2026-11-06',
        'comparison_end': '2026-11-10', 'calendar': 'xshg', 'verified_consecutive_sessions': True,
    }
    result = run_story(payload)
    assert result['editorial']['selection_status'] == 'deterministic_fallback'
    assert '17%' in result['title'] and re.search(crossing, result['title'])
    assert '连续2个交易日' in result['dynamic_body']
    assert all(f'{value}%' in result['dynamic_body'] for value in (low, high))


def test_turnover_does_not_claim_an_unverified_streak(monkeypatch):
    monkeypatch.setattr(turnover_draft_ai, 'request_draft', lambda *_: {'status': 'ok', 'draft': {
        'title': '{{streak}}', 'body': '{{date}}，{{low_turnover}}。{{high_turnover}}。{{reference_line}}。'}})
    payload = turnover_payload(('2026-11-09', '66.37', '16.71'), ('2026-11-06', '66.24', '16.76'))
    payload['facts']['editorial_features'] = {
        'trend_direction': 'cooling', 'trend_streak': 3, 'comparison_end': '2026-11-09',
        'verified_consecutive_sessions': False,
    }
    result = run_story(payload)
    assert result['editorial']['selection_status'] == 'deterministic_fallback'
    assert '连续3个交易日' not in result['title'] + result['dynamic_body']


@pytest.mark.parametrize('previous_high,phrase', [
    ('16.99', '升至17%参考线'),
    ('17.00', '持平于17%参考线'),
])
def test_turnover_at_reference_line_does_not_claim_a_crossing(monkeypatch, previous_high, phrase):
    monkeypatch.setattr(turnover_draft_ai, 'request_draft', lambda *_: {'status': 'not_configured'})
    payload = turnover_payload(('2026-11-10', '66.37', '17.00'), ('2026-11-09', '66.37', previous_high))
    result = run_story(payload)
    assert phrase in result['dynamic_body']
    assert '升破' not in result['title'] + result['dynamic_body']
    assert '跌破' not in result['title'] + result['dynamic_body']


def test_turnover_prompt_tokens_are_rejected_when_the_model_uses_source_field_names(monkeypatch):
    monkeypatch.setattr(turnover_draft_ai, 'request_draft', lambda *_: {'status': 'ok', 'draft': {
        'title': '{{high_title}}',
        'body': '{{date}}，{{low_ma9}}。{{high_ma9}}。'}})
    result = run_story(turnover_payload(('2026-11-09', '66.37', '16.71'), ('2026-11-06', '66.24', '16.76')))
    assert result['editorial']['candidate']['draft_method'] == 'facts_fallback'
    assert result['editorial']['selector']['reason'] == 'turnover_draft_invalid_token'
    assert '66.37%' in result['dynamic_body'] and '16.71%' in result['dynamic_body']


@pytest.mark.parametrize('claim', ['回升至19.99%。', '创三个月新高。', '因外资回流。', '{{low_turnover}}。'])
def test_turnover_rejects_unbound_claims_and_does_not_save_the_rejected_draft(monkeypatch, claim):
    monkeypatch.setattr(turnover_draft_ai, 'request_draft', lambda *_: {'status': 'ok', 'draft': {
        'title': '{{high_title}}', 'body': '{{date}}，{{high_turnover}}。{{low_turnover}}。{{reference_line}}。' + claim}})
    result = run_story(turnover_payload(('2026-11-09', '66.37', '16.71'), ('2026-11-06', '66.24', '16.76')))
    assert result['editorial']['selection_status'] == 'deterministic_fallback'
    assert claim not in result['dynamic_body']
    assert 'draft' not in result['editorial']['selector']


def test_turnover_model_transport_passes_the_free_draft_into_the_normal_entry_point(monkeypatch):
    monkeypatch.setattr(turnover_draft_ai, 'get_settings', lambda: SimpleNamespace(briefs_skill_ai_enabled=True))
    monkeypatch.setattr(turnover_draft_ai, 'briefs_ai', lambda _: {
        'key': 'synthetic-key', 'model': 'mock-draft-model', 'provider': 'openai',
        'base_url': 'https://draft-provider.invalid/v1'})

    def response(request, timeout):
        assert request.full_url == 'https://draft-provider.invalid/v1/chat/completions'
        assert timeout == 30
        model_request = json.loads(request.data)
        system = model_request['messages'][0]['content']
        evidence = json.loads(model_request['messages'][1]['content'])
        assert evidence['data_date'] == '2026-11-09' and evidence['examples']
        assert 'headlines' not in evidence and 'fact_bank' not in evidence
        for name in evidence['required_body_tokens']:
            assert '{{' + name + '}}' in system
        assert '{{low_ma9}}' not in system and '{{high_ma9}}' not in system
        assert set(evidence['required_body_tokens']) <= set(evidence['facts'])
        draft = {'title': '{{reference_title}}', 'body':
                 '{{date}}，{{reference_line}}。\n\n具体来看，{{high_turnover}}。同期，{{low_turnover}}。'}
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(draft, ensure_ascii=False)}}]}, ensure_ascii=False).encode('utf-8'))

    monkeypatch.setattr(turnover_draft_ai, 'urlopen', response)
    result = run_story(turnover_payload(('2026-11-09', '66.37', '16.71'), ('2026-11-06', '66.24', '16.76')))
    assert result['editorial']['candidate']['draft_method'] == 'bound_prose', result['editorial']['selector']
    assert result['title'].endswith('差距扩大至0.29个百分点')
    assert '\n\n具体来看，' in result['dynamic_body']
    assert 'synthetic-key' not in str(result['editorial'])
