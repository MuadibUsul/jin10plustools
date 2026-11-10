"""Sample-informed writing plans with immutable, program-verified fact clauses.

The model chooses the editorial focus, sentence variants and paragraph structure.
It never supplies factual prose or numbers. Every required clause is rendered
exactly once from the validated observation snapshot, including on AI failure.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
import re


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def _turnover_story(facts, scenario, streak):
    """Offer distinct leads from the validated nine-session averages."""
    prefix = '【A股温度计】'
    current = {key: Decimal(str(facts[f'{key}_ma9'])) for key in ('high', 'low')}
    prior = {key: Decimal(str(facts[f'previous_{key}_ma9'])) for key in current}
    delta = {key: current[key] - prior[key] for key in current}
    high, old_high = current['high'], prior['high']
    line = Decimal('17')
    gap, old_gap = abs(high - line), abs(old_high - line)
    line_event = high != old_high and min(high, old_high) <= line <= max(high, old_high)
    closer = gap < old_gap
    if high == line:
        position = '升至' if old_high < line else '降至' if old_high > line else '持平于'
        threshold = f'高换手占比均线{position}17%参考线'
    elif old_high < line < high or high < line < old_high:
        crossing = '升破' if high > line else '跌破'
        threshold = f'高换手占比均线{crossing}17%参考线'
    elif old_high == line:
        threshold = '高换手占比均线' + ('升至17%参考线上方' if high > line else '降至17%参考线下方')
    else:
        threshold = '高换手占比均线' + ('仍高于' if high > line else '仍低于') + '17%参考线'
    distance = '收窄至' if closer else '扩大至' if gap > old_gap else '为'
    comparison = threshold
    if gap:
        comparison += f'，目前相差{gap:.2f}个百分点' if line_event else f'，差距{distance}{gap:.2f}个百分点'

    clauses = []
    headlines = []
    focus = sorted(current, key=lambda key: abs(delta[key]), reverse=True)
    if line_event:
        headlines.append(threshold)
        clauses.append(('comparison', (comparison + '。',)))
    if line_event or closer:
        focus = ['high', 'low']
    if streak and scenario in ('warming', 'cooling') and streak.get('trend_direction') == scenario:
        direction = '回暖' if scenario == 'warming' else '降温'
        trend = f"交投活跃度连续{streak['trend_streak']}个交易日{direction}"
        headlines.append(trend)
        clauses.append(('trend', (f'换手率均线显示{trend}。',)))
    if closer and not line_event:
        headlines.append(comparison)
    for key in focus:
        value, change = current[key], delta[key]
        noun, definition = ('高换手', '高于5%') if key == 'high' else ('低换手', '低于3%')
        metric = f'A股换手率{definition}的股票占比的9日均线'
        move = '上升' if change > 0 else '下降'
        endpoint = ('升至' if change > 0 else '降至' if change < 0 else '持平于') + f'{value:.2f}%'
        if change:
            magnitude = f'{abs(change):.2f}个百分点'
            variants = (f'{metric}{endpoint}，较前一交易日{move}{magnitude}。',
                        f'{metric}较前一交易日{move}{magnitude}，至{value:.2f}%。')
            headlines.append(f'{noun}个股占比均线{endpoint}，单日{move}{magnitude}')
        else:
            variants = (f'{metric}较前一交易日{endpoint}。',)
        clauses.append((f'{key}_turnover', variants))
    if scenario == 'unchanged':
        headlines.append(f'两项换手率占比均线持平，高换手占比均线报{high:.2f}%')
    if gap > old_gap and not line_event:
        headlines.append(comparison)
    if not line_event:
        clauses.append(('comparison', (comparison + '。',)))
    return clauses, [prefix + title for title in headlines]


def fact_bank(category,payload,context,candidate):
    slots=context['slots'];facts=payload['facts'];bank=[]
    extra=facts.get('editorial_features') or {}
    verified_trend=(extra.get('verified_consecutive_sessions') is True
        and isinstance(extra.get('trend_streak'),int) and not isinstance(extra.get('trend_streak'),bool)
        and extra.get('trend_streak',0)>=2 and extra.get('comparison_end')==payload['data_date'])
    combined_trend=False
    def add(key,text,variants=(),required=True):
        if text:
            alternatives=[]
            for old,new in (('Token支出价格指数报','Token支出价格指数最新为'),
                ('一周上涨','最近一周上涨'),('一周下跌','最近一周下跌'),
                ('收盘加权利率为','收盘加权利率报'),('最新成交利率为','最新一笔成交利率为'),
                ('WTI（USO）名义敞口估算约','WTI基金USO的名义敞口估算约'),
                ('Brent（BNO）期货持仓约','Brent基金BNO的期货持仓约'),
                ('标普500等权指数与市值加权指数之差较前一交易日','标普500等权与市值加权指数的差值较前一交易日'),
                ('表现介于三大股指之间','当日涨跌幅介于三大股指之间')):
                if old in text:alternatives.append(text.replace(old,new))
            if key in ('b200','h100','a100'):
                alternatives.append(re.sub(r'(英伟达)?(B200|H100|A100)(?:租赁价格)?(?:升至|降至|报)',
                    lambda m:(m.group(1) or '')+m.group(2)+'最新租赁价格为',text))
            bank.append({'id':key,'required':required,'variants':[{'id':key+'.'+str(i),'text':v} for i,v in enumerate(dict.fromkeys([text,*variants,*alternatives]))]})
    if category == 'turnover':
        clauses, headlines = _turnover_story(facts, context['scenario'], extra if verified_trend else None)
        for key, variants in clauses:
            add(key, variants[0], variants[1:])
        return bank, [{'id': f'title.{i}', 'text': title} for i, title in enumerate(headlines)]
    if category in ('volume','valuation','liquidity','oil_etf'):
        main=slots['main_fact']
        if category=='valuation':
            first,sep,last=main.partition('；')
            add('buffett',first+'。' if sep else first)
            if sep:add('erp',last)
        elif category=='liquidity':
            for i,part in enumerate(filter(None,main.split('。'))):add('rate'+str(i),part+'。')
        else:
            variant=main
            if category=='volume':
                variant=main.replace('沪深两市成交额为','沪深两市成交额'+('回升至' if Decimal(facts['change_yi'])>0 else '回落至' if Decimal(facts['change_yi'])<0 else '维持在'))
                variant=variant.replace('较前一有效交易日增加','较前一交易日放量').replace('较前一有效交易日减少','较前一交易日缩量')
            add('primary',variant,(main,))
        comparison=slots['comparison_fact']
        if verified_trend and category=='volume' and extra['trend_streak']==facts.get('streak'):
            move='回升' if extra['trend_direction']=='up' else '回落'
            side='低于' if Decimal(str(facts['amount_yi']))<Decimal(str(facts['ma20_yi'])) else '不低于'
            comparison=f"最近{extra['trend_streak']}个交易日成交额连续{move}，且均{side}20日均线（{Decimal(str(facts['ma20_yi']))/10000:.2f}万亿元）。"
            combined_trend=True
        add('comparison',comparison)
    elif category=='gpu_rental':
        add('b200',slots['b200_fact']+slots['peak_tail']+'。')
        add('h100',slots['h100_fact']+'。');add('a100',slots['a100_fact']+'。')
    else:
        for key in ('indices','primary','buffett','erp','range','peak','breadth','small_caps','verdict'):
            if key in slots:
                value=slots[key]
                add(key,value if not value or value.endswith(('。','；')) else value+'。')
    if verified_trend and not combined_trend and category=='volume':
        direction=extra['trend_direction'];count=extra['trend_streak']
        if isinstance(count,int) and not isinstance(count,bool) and direction in ('up','down'):
            move='回升' if direction=='up' else '回落'
            add('trend',f'成交额已连续{count}个交易日{move}。')
    summaries={
        'volume':{'up':'当日交投较前一交易日回暖。','down':'当日交投较前一交易日降温。','flat':'当日成交规模与前一交易日基本持平。'},
        'valuation':{'higher':'按ERP口径，权益资产相对国债的估值优势较前期增强。','lower':'按ERP口径，权益资产相对国债的估值优势较前期减弱。','unchanged':'按ERP口径，股债之间的相对估值位置基本稳定。'},
        'oil_etf':{'both_up':'两只基金的期货持仓均有所增加。','both_down':'两只基金的期货持仓均有所减少。','both_flat':'两只基金的期货持仓均与比较日持平。','mixed':'两只基金的期货持仓变化出现分化。'},
    }
    summary=summaries.get(category,{}).get(context['scenario'])
    if summary and not verified_trend:add('reading',summary,required=False)
    headlines=[candidate['title']]
    prefix=candidate['title'].split('】',1)[0]+'】'
    if category=='volume':
        verb='回升' if Decimal(facts['change_yi'])>0 else '回落' if Decimal(facts['change_yi'])<0 else '持平'
        headlines.insert(0,f"{prefix}两市成交额{verb}至{Decimal(facts['amount_yi'])/10000:.2f}万亿元")
    elif category=='valuation':
        latest=Decimal(str(facts['erp_latest']['erp_percent']));previous=Decimal(str(facts['erp_previous']['erp_percent']))
        if previous>=6>latest:headlines.insert(0,f'{prefix}ERP回落至6%参考线下方，股债估值优势减弱')
        elif previous<6<=latest:headlines.insert(0,f'{prefix}ERP重返6%参考线，股债估值优势增强')
        else:headlines.insert(0,f"{prefix}沪深300ERP{'升至' if latest>previous else '降至' if latest<previous else '维持'}{latest:.2f}%")
    elif category=='liquidity':
        quotes={r['product']:r for r in facts['quotes']['rows']}
        headlines.insert(0,f"{prefix}DR007收盘加权利率报{quotes['DR007']['weighted_rate']}%")
        policy=context.get('bindings',{}).get('policy')
        if policy:
            spread=Decimal(policy['spread_bp']);term='隔夜' if policy['term_days']==1 else '7天期'
            headlines.insert(0,f"{prefix}{policy['comparison_product']}{'高于' if spread>0 else '低于' if spread<0 else '持平于'}{term}逆回购操作利率"+(f'{abs(spread):.2f}个基点' if spread else ''))
    elif category=='oil_etf':
        fund={row['ticker']:row for row in facts['funds']}
        if all(fund.get(k,{}).get('change_barrels') is not None for k in ('USO','BNO')):
            def movement(k):
                value=Decimal(str(fund[k]['change_barrels']))
                return '增仓' if value>0 else '减仓' if value<0 else '持仓不变'
            headlines.insert(0,f"{prefix}USO期货{movement('USO')}，BNO期货{movement('BNO')}")
    elif category=='token_cost' and facts.get('weekly_change_pct') is not None:
        change=Decimal(str(facts['weekly_change_pct']));move='上涨' if change>0 else '下跌' if change<0 else '持平'
        suffix='，较此前已收录高点回落逾五成' if Decimal(str(facts.get('drawdown_pct') or 0))>50 else ''
        headlines.insert(0,f"{prefix}Token价格一周{move}{abs(change)}%{suffix}")
    elif category=='gpu_rental':
        change=facts.get('b200_change_pct')
        if change is not None:
            value=Decimal(str(change));move='上涨' if value>0 else '下跌' if value<0 else '持平'
            headlines.insert(0,f"{prefix}B200租赁价格一周{move}{abs(value)}%")
    elif category=='us_barometer':
        actual=date.fromisoformat(payload['data_date'])
        headlines.insert(0,f"{prefix}美股{actual.month}月{actual.day}日投资性价比：{facts['verdict']}")
    elif category=='us_structure':
        actual=date.fromisoformat(payload['data_date'])
        headlines.insert(0,f"{prefix}美股{actual.month}月{actual.day}日市场结构：{facts['verdict']}")
    if verified_trend and category=='volume':
        move='回升' if extra['trend_direction'] in ('up','warming') else '回落'
        headlines.insert(0,f"{prefix}两市成交额连续{extra['trend_streak']}个交易日{move}")
    return bank,[{'id':'title.'+str(i),'text':v} for i,v in enumerate(dict.fromkeys(headlines))]


def validate_plan(context,plan):
    if not isinstance(plan,dict) or set(plan)!={'headline','paragraphs'}:raise ValueError('invalid_story_plan')
    if plan['headline'] not in {r['id'] for r in context['headlines']}:raise ValueError('invalid_story_headline')
    paragraphs=plan['paragraphs']
    if not isinstance(paragraphs,list) or not 1<=len(paragraphs)<=4 or any(not isinstance(p,list) or not p for p in paragraphs):raise ValueError('invalid_story_paragraphs')
    variants={v['id']:f['id'] for f in context['fact_bank'] for v in f['variants']}
    ids=[value for p in paragraphs for value in p]
    if any(not isinstance(value,str) or value not in variants for value in ids):raise ValueError('unknown_story_fact')
    facts=[variants[value] for value in ids]
    required={f['id'] for f in context['fact_bank'] if f.get('required',True)}
    if len(facts)!=len(set(facts)) or not required<=set(facts):raise ValueError('story_fact_coverage')
    if context['category']=='us_structure' and facts[0]!='indices':raise ValueError('indices_must_open')
    if context['category']=='gpu_rental' and facts[:3]!=['b200','h100','a100']:
        raise ValueError('gpu_story_order')
    if context['category'] in ('us_structure','us_barometer'):
        groups=[{variants[value] for value in group} for group in paragraphs]
        expected=([{'indices','breadth'},{'small_caps','verdict'}] if context['category']=='us_structure'
                  else [{'buffett'},{'erp','verdict'}])
        if groups!=expected:raise ValueError('us_story_layout')
    return plan


def render_plan(context,plan):
    validate_plan(context,plan)
    variants={v['id']:v['text'] for f in context['fact_bank'] for v in f['variants']}
    title=next(r['text'] for r in context['headlines'] if r['id']==plan['headline'])
    groups=deepcopy(plan['paragraphs'])
    # A redundant one-line conclusion never becomes a separate paragraph.
    if len(groups)>1 and len(groups[-1])==1 and groups[-1][0].startswith('reading.'):
        conclusion=groups.pop()
        groups[-1].extend(conclusion)
    paragraphs=[''.join(variants[v] for v in group) for group in groups]
    if context['category'] in ('us_structure','us_barometer'):
        paragraphs=[str(i)+'、'+text for i,text in enumerate(paragraphs,1)]
    else:paragraphs[0]=context['printed_day']+'，'+paragraphs[0]
    return title,'\n\n'.join(paragraphs)


def compose(payload,category,team_settings,pack,candidate):
    from .editorial_suite import _context
    from .skill_library import history_samples,effective
    from .gpu_editorial_ai import select_plan
    from .policy import CATEGORIES
    from types import SimpleNamespace
    if category=='gpu_rental':
        from .gpu_editorial import _facts,_span,_peak_span,_month_day
        f=_facts(payload);peak=_peak_span(f)
        context={'scenario':candidate['scenario'],'slots':{'b200_fact':_span('b200',f),'peak_tail':'，'+peak if peak else '',
            'h100_fact':_span('h100',f),'a100_fact':_span('a100',f),'data_day':_month_day(f['actual'])}}
    else:context=_context(category,payload)
    bank,headlines=fact_bank(category,payload,context,candidate)
    skill=effective(SimpleNamespace(settings=team_settings or {}),category)
    samples=history_samples(category)
    # Keep actual sample prose, including scenarios different from this session.
    examples=samples[:8] if category in ('turnover','token_cost','gpu_rental') else samples[-8:]
    options={'schema':'story_v2','category':category,'label':CATEGORIES[category],
        'scenario':context['scenario'],'facts':deepcopy(payload['facts']),
        'bindings':deepcopy(candidate.get('bindings',{})),
        'instructions':skill['instructions'],'examples':examples,'fact_bank':bank,'headlines':headlines,
        'writing_order':context.get('writing_order','先交代B200与已收录高点关系，再对照H100和A100。'),
        'paragraph_rules':({'first':['indices','breadth'],'second':['small_caps','verdict']} if category=='us_structure' else
            {'first':['buffett'],'second':['erp','verdict']} if category=='us_barometer' else {}),
        'printed_day':context['slots'].get('date') or context['slots'].get('data_day') or payload['data_date']}
    ids=[row['variants'][0]['id'] for row in bank if row.get('required',True)]
    # Facts first; the last comparison/reading forms a separate, supported paragraph.
    split=1
    fallback={'headline':headlines[0]['id'],'paragraphs':[ids[:split],ids[split:]] if category in ('liquidity','gpu_rental','us_barometer') and len(ids)>split else [ids]}
    if category=='us_structure' and len(ids)>2:fallback['paragraphs']=[ids[:2],ids[2:]]
    plan=fallback
    if category=='turnover':
        from .turnover_draft import build_context, render_draft
        from .turnover_draft_ai import request_draft
        draft_context=build_context(payload,examples,skill['instructions'])
        result=request_draft(team_settings,draft_context)
        if result.get('status')=='ok':
            try:
                title,body=render_draft(draft_context,result.get('draft'))
                plan=None
            except ValueError as exc:
                result={**result,'status':'failed','reason':str(exc)}
    else:
        result=select_plan(team_settings,options)
        if result.get('status')=='ok':
            try:plan=validate_plan(options,result['plan'])
            except (ValueError,KeyError,TypeError):result={**result,'status':'failed','reason':'invalid_story_plan'}
    if plan is not None:
        title,body=render_plan(options,plan)
    updated={**candidate,'title':title,'body':body,'writer_schema':2,'plan':deepcopy(plan),
        'sample_digest':sha(examples),'sample_ids':[s['date'] for s in examples],
        'instruction_digest':skill['digest'],'facts_digest':sha(payload['facts']),
        'fact_bank_snapshot':bank,'headline_snapshot':headlines,'hard_checks':{'facts':True,'structure':True,'all_required_facts_once':True},
        'style_quality':{'structural_validation':'passed','human_acceptance':'pending'}}
    if category=='turnover':
        updated['draft_method']='bound_prose' if plan is None else 'facts_fallback'
        if plan is None:
            updated['draft_template']=deepcopy(result['draft'])
            updated['draft_facts']=deepcopy(draft_context['facts'])
    return updated,{k:v for k,v in result.items() if k not in ('plan','draft')},options


def apply_sample_story(payload,category,team_settings):
    from .editorial_authorization import resolve_pack,review_basis
    from .editorial_suite import build_candidate
    from .delivery import complete_body
    from app.config import get_settings
    saved=((team_settings or {}).get('skills') or {}).get(category) or {}
    if payload.get('body_template_custom') or saved.get('enabled') is False:return False
    if category=='gpu_rental' and get_settings().briefs_gpu_editorial_mode=='off':return False
    baseline={'title':payload['title'],'body':payload['dynamic_body']}
    try:
        pack,pending=resolve_pack(category,team_settings)
        candidate=build_candidate(category,payload,pack=pack)
        candidate,selector,context=compose(payload,category,team_settings,pack,candidate)
        authorization=review_basis(pack,category,team_settings)
        candidate['style_review']=authorization
        payload['title']=candidate['title'];payload['dynamic_body']=candidate['body']
        payload['rule_copy']=baseline
        payload['editorial']={'mode':'active','status':'active','writer':'sample_story_v2','baseline':baseline,
            'candidate':candidate,'authorization':authorization,'selector':selector,'pending_pack_version':pending,
            'selection_status':'model_selected' if selector.get('status')=='ok' else 'deterministic_fallback',
            'sample_count':len(context['examples']),'instructions_digest':candidate['instruction_digest']}
        payload['skill']={**selector,'mode':'sample_story_v2','style_status':'active',
            'selection_status':payload['editorial']['selection_status']}
        warnings=[w for w in payload.get('warnings',[]) if not w.startswith(('AI 额度不足，已自动回退固定组合文案','Skill AI 未完成（'))]
        if selector.get('status')!='ok':
            failure='AI写稿未完成' if category=='turnover' else 'AI表达选择未完成'
            warnings.append(failure+'，已采用样本规范的完整事实稿'+('（额度不足）' if selector.get('http_status') in (402,429) else ''))
        if pending:warnings.append('新风格包尚待团队采用，当前继续使用已采用的风格版本')
        if not context['examples']:warnings.append('本栏目尚无可读范文，本期按栏目编辑规范组织；不冒称已学习上传样本')
        payload['warnings']=warnings
        if category in ('token_cost','gpu_rental','liquidity'):
            payload['dynamic_body']=complete_body(category,payload['dynamic_body'],payload.get('fixed_notes'))
        return True
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as exc:
        payload.setdefault('warnings',[]).append('样本写作未生效，已保留完整规则稿：'+str(exc)[:100])
        payload['editorial']={'status':'fallback','reason':str(exc)[:100],'baseline':baseline,'writer':'sample_story_v2'}
        return True
