"""Rules always work offline; optional DeepSeek only chooses fact-safe wording."""
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


def generate_deepseek(rule):
    key=os.getenv('DEEPSEEK_API_KEY','');model=os.getenv('DEEPSEEK_MODEL','')
    if not key or not model:return {'status':'not_configured'}
    payload={'model':model,'stream':False,'max_tokens':768,'response_format':{'type':'json_object'},
             'messages':[{'role':'system','content':'只输出json格式title/body，必须原样选用用户提供的一条标题和一条正文，不得改动数字或增加政策判断。'},
                         {'role':'user','content':json.dumps({'titles':rule['approved_titles'],'bodies':rule['approved_bodies']},ensure_ascii=False)}]}
    try:
        request=Request('https://api.deepseek.com/chat/completions',data=json.dumps(payload).encode(),
                        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
        with urlopen(request,timeout=30) as response:raw=response.read(131073)
        if len(raw)>131072:return {'status':'invalid_response'}
        data=json.loads(raw);choice=data['choices'][0]
        if choice.get('finish_reason')!='stop':return {'status':'invalid_response'}
        answer=json.loads(choice['message']['content'])
        if (set(answer)!={'title','body'} or answer['title'] not in rule['approved_titles'] or
                answer['body'] not in rule['approved_bodies']):return {'status':'validation_failed'}
        return {'status':'ok',**answer}
    except HTTPError as exc:return {'status':'api_error','http_status':exc.code}
    except (TimeoutError,URLError,OSError):return {'status':'network_error'}
    except (ValueError,KeyError,IndexError,TypeError):return {'status':'invalid_response'}
