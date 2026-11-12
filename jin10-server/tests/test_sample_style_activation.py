import copy,json
from pathlib import Path

import pytest
from app.briefs import editorial_suite,skill_production
from app.briefs.body_templates import apply as apply_template

CASES=json.loads((Path(__file__).parent/'fixtures/editorial_suite_cases.json').read_text(encoding='utf-8'))['cases']
CATEGORIES=sorted(editorial_suite.SUPPORTED_CATEGORIES)


def source(category):
    return copy.deepcopy(next(c['payload'] for c in CASES if c['category']==category and c['expect']['status']=='ok'))


def authorization(category):
    return {'brief_editorial':{'mode':'sample','packs':{category:{
        'digest':editorial_suite.load_pack(category)['digest'],
        'authorized_by':'workspace_user','authorized_at':'2026-11-03T00:00:00+00:00',
        'source':'explicit_user_instruction'}}}}


@pytest.mark.parametrize('category',CATEGORIES)
def test_explicit_sample_choice_keeps_style_during_ai_quota_failure(category,monkeypatch):
    from app.briefs import gpu_editorial_ai, turnover_draft_ai

    def unavailable(*args, **kwargs):
        return {'status': 'failed', 'reason': 'provider_http_error', 'http_status': 402}

    if category == 'turnover':
        monkeypatch.setattr(turnover_draft_ai, 'request_draft', unavailable)
    else:
        monkeypatch.setattr(gpu_editorial_ai, 'select_plan', unavailable)
    def no_legacy_polish(*a,**kw):raise AssertionError('Do not replace styled copy with the legacy polishing stage')
    monkeypatch.setattr(skill_production,'polish',no_legacy_polish)
    payload=source(category);original=copy.deepcopy(payload)
    result=skill_production.apply_skill_copy(payload,category,authorization(category))
    assert result['editorial']['status']=='active'
    assert result['editorial']['authorization']=='team_authorized_pack'
    assert result['editorial']['selection_status']=='deterministic_fallback'
    assert result['skill']['status']=='failed' and result['skill']['style_status']=='active'
    candidate=result['editorial']['candidate']
    assert result['title']==candidate['title'] and result['dynamic_body']==candidate['body']
    assert result['facts']==original['facts'] and result['data_date']==original['data_date']
    assert result['images']==original['images'] and result['fixed_notes']==original['fixed_notes']
    assert '额度不足' in ''.join(result['warnings'])


@pytest.mark.parametrize('category',CATEGORIES)
def test_pack_revision_needs_its_own_team_authorization(category):
    settings=authorization(category)
    settings['brief_editorial']['packs'][category]['digest']='outdated'
    payload=source(category);original=copy.deepcopy(payload)
    editorial_suite.apply_editorial(payload,category,settings)
    assert payload['title']==original['title'] and payload['dynamic_body']==original['dynamic_body']
    assert payload['editorial']['status']=='shadow'
    assert payload['editorial']['reason']=='pack_not_reviewed'


def test_switching_to_builtin_clears_a_stale_custom_template_marker():
    payload=source('valuation');payload['body_template_custom']=True
    saved={'brief_templates':{'valuation':{'use_builtin':True,'template':'保存但不采用'}}}
    apply_template(payload,'valuation',saved)
    assert 'body_template_custom' not in payload and saved['brief_templates']['valuation']['template']=='保存但不采用'


def test_valuation_style_preserves_the_previous_erp_number_in_real_template_shape():
    payload=source('valuation');facts=payload['facts']
    payload['dynamic_body']=f"{payload['data_date']}，巴菲特指数为{facts['buffett_percent']}%；沪深300ERP为{facts['erp_latest']['erp_percent']}%，前值为{facts['erp_previous']['erp_percent']}%。"
    candidate=editorial_suite.build_candidate('valuation',payload)
    assert f"前值为{float(facts['erp_previous']['erp_percent']):.2f}%" in candidate['body']


def test_valuation_sub_display_change_does_not_claim_a_zero_point_drop():
    from app.briefs.engines.valuation.calculate import display_movement
    from app.briefs.engines.valuation.run_erp import rule_copy
    from app.briefs.sample_story import fact_bank

    previous = '5.832920505200594353640416048'
    current = '5.831044237918215613382899628'
    assert display_movement(current, previous) == (
        'unchanged', '按两位小数显示持平（原始值略降）')
    assert display_movement(previous, previous) == ('unchanged', '持平（变动0.00个百分点）')

    payload = source('valuation')
    payload['data_date'] = '2026-11-11'
    payload['facts']['buffett_percent'] = '84.69'
    payload['facts']['erp_latest'] = {'date': '2026-11-11', 'erp_percent': current}
    payload['facts']['erp_previous'] = {'date': '2026-11-10', 'erp_percent': previous}
    payload['dynamic_body'] = ('2026-11-11，巴菲特指数（A股总市值/上年度全年GDP）为84.69%；'
        '沪深300指数股权风险溢价（ERP）为5.83%，较2026-11-10按两位小数显示持平（原始值略降），'
        '低于6%参考线。权益资产相对国债的估值优势较前日变化不大。')

    candidate = editorial_suite.build_candidate('valuation', payload)
    assert candidate['scenario'] == 'unchanged'
    assert '按两位小数显示持平（原始值略降）' in candidate['body']
    bank, headlines = fact_bank('valuation', payload, editorial_suite._context('valuation', payload), candidate)
    assert bank and '沪深300ERP维持在5.83%' in headlines[0]['text']
    assert '下降0.00' not in candidate['body']

    rows = [{'date': '2026-11-10', 'erp_percent': previous, 'erp': '0.058329205052'},
            {'date': '2026-11-11', 'erp_percent': current, 'erp': '0.058310442379'}]
    assert '下降0.00' not in rule_copy(rows, 'free-official')
