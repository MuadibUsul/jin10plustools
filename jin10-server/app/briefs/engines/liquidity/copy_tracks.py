"""Rules always work offline; the optional AI provider only chooses fact-safe wording."""
import json
import os
from decimal import Decimal,ROUND_HALF_UP
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError


def build_copy(snapshot):
    quotes=snapshot['quotes'];row=next(r for r in quotes['rows'] if r['product']=='DR007')
    current=Decimal(row['weighted_rate'])
    previous=next((r for r in reversed(snapshot['history']['rows']) if r['date']<quotes['quote_date'] and r.get('DR007') is not None),None)
    fact=f"截至{quotes['updated_at']}，DR007加权利率为{row['weighted_rate']}%"
    if row['latest_rate'] is not None:fact+=f"，最新成交利率为{row['latest_rate']}%"
    if previous:
        change=(current-Decimal(previous['DR007']))*100
        change_abs=abs(change).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        direction='下行' if change<0 else '上行' if change>0 else '持平'
        fact+=(f"；较{previous['date']}的日加权利率{direction}"+
               (f'{change_abs}个基点' if change else ''))
        assessment=('短端回购融资利率较上一日有所回落。' if change<0 else
                    '短端回购融资利率较上一日有所上升。' if change>0 else '短端回购融资利率较上一日保持平稳。')
    else:
        direction='更新';assessment='该数据反映当前时点的短端回购融资利率。'
    fact+='。'
    titles=[f'【流动性指标】DR007加权利率{direction}至{row["weighted_rate"]}%' if direction not in ('持平','更新') else
            f'【流动性指标】DR007加权利率报{row["weighted_rate"]}%',
            f'【流动性指标】DR007最新加权利率为{row["weighted_rate"]}%']
    bodies=[fact+assessment,fact+'以上为当前时点的回购利率表现。']
    return {'status':'ok','title':titles[0],'body':bodies[0],
            'approved_titles':titles,'approved_bodies':bodies,'policy_comparison_included':False}


def parse_answer(content):
    """Accept plain JSON, fenced JSON, or chat2api's <escape> wrapper."""
    text=content.strip()
    if text.startswith('<escape>') and text.endswith('</escape>'):
        text=text[len('<escape>'):-len('</escape>')].strip()
    if text.startswith('```'):
        text=text.split('\n',1)[1] if '\n' in text else text
        if text.rstrip().endswith('```'):text=text.rstrip()[:-3]
        text=text.strip()
        if text.startswith('json'):text=text[4:].strip()
    if not text.startswith('{'):
        start,end=text.find('{'),text.rfind('}')
        if start>=0 and end>start:text=text[start:end+1]
    return json.loads(text)


def generate_deepseek(rule):
    provider=os.getenv('AI_PROVIDER','deepseek')
    key=os.getenv('AI_API_KEY') or os.getenv('DEEPSEEK_API_KEY','')
    model=os.getenv('AI_MODEL') or os.getenv('DEEPSEEK_MODEL','')
    base=os.getenv('AI_BASE_URL') or ('https://api.deepseek.com' if provider=='deepseek' else '')
    if not key or not model or not base:return {'status':'not_configured','provider':provider}
    diagnostics={'provider':provider,'model':model,'max_tokens':4096}
    if provider=='deepseek':diagnostics['thinking']='disabled'
    def failed(status,reason,**extra):return {'status':status,'reason':reason,**diagnostics,**extra}
    payload={'model':model,'stream':False,'max_tokens':4096,'response_format':{'type':'json_object'},
             'messages':[{'role':'system','content':'只输出json格式title/body，必须原样选用用户提供的一条标题和一条正文，不得改动数字或增加政策判断。'},
                         {'role':'user','content':json.dumps({'titles':rule['approved_titles'],'bodies':rule['approved_bodies']},ensure_ascii=False)}]}
    if provider=='deepseek':payload['thinking']={'type':'disabled'}
    try:
        request=Request(base.rstrip('/')+'/chat/completions',data=json.dumps(payload).encode(),
                        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
        with urlopen(request,timeout=60) as response:raw=response.read(131073)
        if len(raw)>131072:return failed('invalid_response','response_too_large')
        data=json.loads(raw);choice=data['choices'][0]
        diagnostics['finish_reason']=choice.get('finish_reason')
        diagnostics['usage']={k:v for k,v in (data.get('usage') or {}).items() if k in ('prompt_tokens','completion_tokens','total_tokens') and isinstance(v,int)}
        if choice.get('finish_reason')!='stop':return failed('invalid_response','output_truncated' if choice.get('finish_reason')=='length' else 'incomplete_completion')
        if not choice.get('message',{}).get('content'):return failed('invalid_response','empty_content')
        answer=parse_answer(choice['message']['content'])
        if (set(answer)!={'title','body'} or answer['title'] not in rule['approved_titles'] or
                answer['body'] not in rule['approved_bodies']):return failed('validation_failed','unapproved_wording')
        return {'status':'ok',**answer,**diagnostics}
    except HTTPError as exc:return failed('api_error','provider_http_error',http_status=exc.code)
    except (TimeoutError,URLError,OSError):return failed('network_error','request_failed')
    except (ValueError,KeyError,IndexError,TypeError):return failed('invalid_response','invalid_json_or_schema')
