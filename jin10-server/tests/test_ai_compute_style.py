"""AI-compute Skill copy must sound like the style corpus and keep fact numbers."""
from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.briefs import ai_compute, ai_compute_style, skill_ai, skill_library, skill_production
from app.briefs.delivery import FIXED_NOTES


def token_facts(**overrides):
    facts = {
        'token_price': '0.9750', 'previous_date': '2026-10-07', 'previous_price': '0.9931',
        'weekly_change_pct': '-1.82', 'range_low': '0.9750', 'range_high': '0.9931',
        'range_count': 2, 'peak_price': '0.9931', 'peak_date': '2026-10-07',
        'prior_peak_price': '0.9931', 'prior_peak_date': '2026-10-07',
        'peak_relation': 'below', 'drawdown_pct': '1.82',
    }
    facts.update(overrides)
    return facts


def gpu_facts(**overrides):
    facts = {
        'a100': '1.52', 'h100': '2.81', 'b200': '6.01', 'previous_date': '2026-10-08',
        'a100_change_pct': '-0.65', 'h100_change_pct': '+0.36', 'b200_change_pct': '+2.21',
        'b200_peak': '6.11', 'b200_peak_date': '2026-07-01',
        'b200_peak_relation': 'below', 'b200_gap_pct': '1.64',
    }
    facts.update(overrides)
    return facts


def payload(category, facts, day):
    return {
        'title': '事实标题', 'dynamic_body': '事实正文。', 'data_date': day,
        'facts': facts, 'warnings': [],
    }


def test_history_corpus_is_loaded_whole_and_not_treated_as_live_facts():
    token = skill_ai._history('token_cost')
    gpu = skill_ai._history('gpu_rental')
    assert '不作为当期事实' in token and '不作为当期事实' in gpu
    assert token.count('### 风格样本 ') == 18
    assert gpu.count('### 风格样本 ') == 11
    assert '【AI算力CPI】Token价格波幅收窄，趋势是否扭转？' in token
    assert '【AI景气度指标】高端GPU租赁价高位波动，代际价格分化持续' in gpu
    volume = skill_ai._history('volume')
    assert '### 2026-10-20' in volume and '检索日' not in volume

    token_samples = skill_library.history_samples('token_cost')
    gpu_samples = skill_library.history_samples('gpu_rental')
    assert [item['date'] for item in token_samples] == [f'风格样本 {i:02d}' for i in range(1, 19)]
    assert len(gpu_samples) == 11
    assert token_samples[0]['title'].startswith('【AI算力CPI】')
    assert '截至10月19日' in token_samples[0]['body']
    assert '数据来自OpenAI' not in token_samples[0]['body']
    assert '注：' not in token_samples[11]['body']
    assert skill_library._count_samples(
        (skill_ai.asset_dir('volume') / 'references' / 'history.md').read_text(encoding='utf-8')) == 20


def test_token_style_uses_corpus_rhythm_without_copying_sample_numbers():
    result = ai_compute_style.apply_compute_style(
        payload('token_cost', token_facts(), '2026-10-14'), 'token_cost')
    assert result['title'] == '【AI算力CPI】Token价格最近一周小幅下跌'
    body = result['dynamic_body']
    assert body.startswith('截至10月14日，Token支出价格报0.9750美元/百万Token。')
    assert '较10月7日的0.9931美元/百万Token下跌1.82%' in body
    assert '累计跌幅为1.82%' in body
    assert '0.9750—0.9931' in body
    assert '波动收窄' not in result['title']
    assert '0.95' not in body and '腰斩' not in result['title']
    assert result['facts_copy']['title'] == '事实标题'
    assert result['style_contract'] == ai_compute_style.STYLE_CONTRACT


def test_token_rebound_question_and_halving_need_the_fact_pack():
    halved = ai_compute_style.apply_compute_style(payload('token_cost', token_facts(
        token_price='0.9800', weekly_change_pct='+1.50', previous_price='0.9655',
        previous_date='2026-10-07', prior_peak_price='2.0651', prior_peak_date='2026-05-28',
        peak_relation='below', drawdown_pct='52.54', range_low='', range_high='', range_count=0,
    ), '2026-10-14'), 'token_cost')
    assert halved['title'] == '【AI算力CPI】Token价格腰斩后一周回升，趋势是否扭转？'
    assert '累计跌幅为52.54%' in halved['dynamic_body']
    assert '止跌' not in halved['title'] + halved['dynamic_body']

    tied = ai_compute_style.apply_compute_style(payload('token_cost', token_facts(
        peak_relation='tie', drawdown_pct='', token_price='2.0651', prior_peak_price='2.0651',
        weekly_change_pct='+0.40',
    ), '2026-10-14'), 'token_cost')
    assert tied['title'] == '【AI算力CPI】Token价格与已收录高点并列'
    assert '并列高位' in tied['dynamic_body']
    assert '新高' not in tied['title'] + tied['dynamic_body']


def test_gpu_style_matches_three_card_rhythm_and_refuses_half_year_high():
    result = ai_compute_style.apply_compute_style(
        payload('gpu_rental', gpu_facts(), '2026-10-15'), 'gpu_rental')
    assert result['title'] == '【AI景气度指标】高端GPU租赁价分化，B200走强、A100走弱'
    body = result['dynamic_body']
    assert body.startswith('截至10月15日，英伟达B200租赁价格报6.01美元/GPU每小时，较七个自然日前（10月8日）上涨2.21%。')
    assert 'H100租赁价格报2.81美元，上涨0.36%' in body
    assert 'A100报1.52美元，下跌0.65%' in body
    assert '三卡价格呈现代际分化' in body
    assert '低1.64%' in body
    assert '创半年新高' not in result['title'] + body
    assert '结构性紧缺' not in body

    ktype = ai_compute_style.apply_compute_style(payload('gpu_rental', gpu_facts(
        b200_change_pct='+1.20', h100_change_pct='0.00', a100_change_pct='-0.80',
        b200_gap_pct='8.00',
    ), '2026-10-15'), 'gpu_rental')
    assert ktype['title'] == '【AI景气度指标】高端GPU租赁价呈K型分化，B200强于前两代'
    assert 'K型分化' in ktype['dynamic_body']
    assert '紧缺' not in ktype['dynamic_body']

    tied = ai_compute_style.apply_compute_style(payload('gpu_rental', gpu_facts(
        b200='6.11', b200_peak_relation='tie', b200_gap_pct='',
    ), '2026-10-15'), 'gpu_rental')
    assert '并列' in tied['title'] and '并列高位' in tied['dynamic_body']
    assert '新高' not in tied['title'] + tied['dynamic_body']

    fresh = ai_compute_style.apply_compute_style(payload('gpu_rental', gpu_facts(
        b200='6.20', b200_peak_relation='above', b200_gap_pct='',
        b200_change_pct='+1.00', h100_change_pct='+0.20', a100_change_pct='+0.10',
    ), '2026-10-15'), 'gpu_rental')
    assert fresh['title'] == '【AI景气度指标】B200租赁价高于此前已收录高点'
    assert '半年新高' not in fresh['title'] + fresh['dynamic_body']


def test_style_rejects_numbers_outside_the_fact_pack_and_keeps_the_fact_copy():
    with pytest.raises(ValueError, match='以外的数字'):
        ai_compute_style.assert_supported(
            payload('token_cost', token_facts(), '2026-10-14'),
            '【AI算力CPI】Token价格', '价格报9.99美元/百万Token。')
    original = payload('token_cost', {'token_price': ''}, '2026-10-14')
    assert ai_compute_style.apply_compute_style(original, 'token_cost')['dynamic_body'] == '事实正文。'


def test_skill_stage_keeps_styled_copy_when_ai_fails_and_respects_a_team_template(monkeypatch):
    monkeypatch.setattr(skill_production, 'polish', lambda *args: {'status': 'failed', 'reason': 'not_configured'})
    styled = skill_production.apply_skill_copy(
        payload('token_cost', token_facts(), '2026-10-14'), 'token_cost', {})
    assert styled['title'].startswith('【AI算力CPI】')
    assert '美元/百万Token' in styled['dynamic_body']
    assert styled['rule_copy']['body'] == styled['dynamic_body']
    assert any('未完成' in warning for warning in styled['warnings'])

    custom = payload('gpu_rental', gpu_facts(), '2026-10-15')
    custom['body_template_custom'] = True
    custom['title'] = '团队标题'
    custom['dynamic_body'] = '团队正文'
    kept = skill_production.apply_skill_copy(custom, 'gpu_rental', {})
    assert kept['title'] == '团队标题' and kept['dynamic_body'] == '团队正文'


def test_adapter_output_is_restyled_without_losing_its_numbers(monkeypatch, tmp_path):
    token_rows = [
        {'date': '2026-10-07', 'token_price': Decimal('0.9931'), 'a100': None, 'h100': None, 'b200': None},
        {'date': '2026-10-14', 'token_price': Decimal('0.9750'), 'a100': None, 'h100': None, 'b200': None},
    ]
    gpu_rows = [
        {'date': '2026-07-01', 'token_price': None, 'a100': Decimal('1.60'), 'h100': Decimal('3.00'), 'b200': Decimal('6.11')},
        {'date': '2026-10-08', 'token_price': None, 'a100': Decimal('1.53'), 'h100': Decimal('2.80'), 'b200': Decimal('5.88')},
        {'date': '2026-10-15', 'token_price': None, 'a100': Decimal('1.52'), 'h100': Decimal('2.81'), 'b200': Decimal('6.01')},
    ]
    monkeypatch.setattr(ai_compute, '_rows', lambda kind: token_rows if kind == 'token' else gpu_rows)
    (tmp_path / 'token').mkdir()
    (tmp_path / 'gpu').mkdir()
    token = ai_compute.token_cost('2026-10-14', tmp_path / 'token', None)
    gpu = ai_compute.gpu_rental('2026-10-15', tmp_path / 'gpu', None)
    styled_token = ai_compute_style.apply_compute_style(token, 'token_cost')
    styled_gpu = ai_compute_style.apply_compute_style(gpu, 'gpu_rental')
    assert '0.9750' in styled_token['dynamic_body'] and '1.82' in styled_token['dynamic_body']
    assert '6.01' in styled_gpu['dynamic_body'] and '1.64' in styled_gpu['dynamic_body']
    assert styled_gpu['facts_copy']['body'] != styled_gpu['dynamic_body']


def test_polish_prompt_contains_the_style_corpus(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit=-1):
            content = json.dumps({'title': '【AI算力CPI】样例标题', 'body': '截至样例。'}, ensure_ascii=False)
            # The approved sentence is whatever the caller sent; echo it back below.
            return self.payload

    def opener(request, timeout):
        captured['body'] = json.loads(request.data.decode())
        answer = {'title': '【AI算力CPI】样例标题', 'body': '截至样例。'}
        raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(answer, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
        response = Response()
        response.payload = raw
        return response

    monkeypatch.setattr(skill_ai, 'get_settings', lambda: SimpleNamespace(briefs_skill_ai_enabled=True))
    monkeypatch.setattr(skill_ai, 'briefs_ai', lambda team: {
        'key': 'test-key', 'model': 'deepseek-chat', 'base_url': 'https://example.test/v1',
        'provider': 'deepseek', 'configured': True})
    monkeypatch.setattr(skill_ai, 'urlopen', opener)
    skill_ai.polish(None, 'token_cost', '【AI算力CPI】样例标题', '截至样例。')
    user = captured['body']['messages'][1]['content']
    assert '不作为当期事实' in user
    assert '风格样本 01' in user
    assert 'Token价格波幅收窄' in user


def test_fixed_notes_are_the_sample_bullets():
    token = FIXED_NOTES['token_cost']
    gpu = FIXED_NOTES['gpu_rental']
    assert token == (
        '1、各大模型降价以及用户从昂贵的前沿模型转向更便宜的开源模型、小模型，推动token价格下跌。\n'
        '2、当前阶段价格下降可能刺激需求增加，这有利于AI应用等板块，对云厂商影响复杂，而对OpenAI、Anthropic等模型厂商来说，定价权下降则是个风险因素。\n'
        '3、数据来自OpenAI、Anthropic、DeepSeek等全球顶尖AI模型。')
    assert gpu == (
        '1、GPU租赁价格指数主要统计每天云服务商、数据中心和私人租赁市场中英伟达A100、H100、B200等GPU的实际租赁报价。\n'
        '2、A100、H100、B200是英伟达从老到新三代芯片产品（GPU），通常代际越新的GPU性能越好、越能适应大规模复杂大模型，租赁价格也往往越高。\n'
        '3、由于GPU价格太高、更新太快等原因，不少企业选择向云服务商、数据中心等租赁芯片，以节省资金投入。相关指数上涨通常意味着GPU更紧缺、需求更旺；下降则说明供应增加或需求降温。')
    assert token.count('\n') == 2 and gpu.count('\n') == 2
    assert '创半年新高' not in gpu
