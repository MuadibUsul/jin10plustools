"""Rules always work offline; the optional AI provider only chooses fact-safe wording."""
import json
import os
from decimal import Decimal,ROUND_HALF_UP
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError


def _points(current,previous):
    delta=(Decimal(str(current))-Decimal(str(previous)))*100
    shown=abs(delta).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    if delta==0:return '持平',f'{shown:.2f}'
    return ('上行' if delta>0 else '下行'),f'{shown:.2f}'


def _prior(snapshot,code,quote_date):
    return next((row for row in reversed(snapshot.get('history',{}).get('rows',[]))
                 if row['date']<quote_date and row.get(code) not in (None,'')),None)


def build_copy(snapshot):
    """Two paragraphs: same-basis daily comparison, then the same-date policy operation.

    Intraday weighted rates may be compared with the previous session's published
    daily weighted rate only when both bases are named. A previous day's operation
    is never described as today's.
    """
    quotes=snapshot['quotes'];rows={row['product']:row for row in quotes.get('rows',[])}
    intraday=quotes.get('timestamp_kind')=='intraday'
    basis='盘中加权利率' if intraday else '收盘加权利率'
    lead=f"截至{quotes.get('updated_at','')}，"
    if intraday:lead+='本次为盘中成交加权，不是当日收盘。'
    fragments=[]
    for code in ('DR001','DR007'):
        row=rows.get(code)
        if not row or row.get('weighted_rate') in (None,''):
            fragments.append(f'{code}本时刻未公布。')
            continue
        fragment=f"{code}{basis}为{row['weighted_rate']}%"
        prior=_prior(snapshot,code,quotes['quote_date'])
        if prior:
            direction,amount=_points(row['weighted_rate'],prior[code])
            fragment+=f"，较{prior['date']}已发布日度加权利率{prior[code]}%"
            fragment+='持平' if direction=='持平' else f'{direction}{amount}个基点'
        else:
            fragment+='，尚无更早的已发布日度加权利率，不算涨跌基点'
        fragments.append(fragment+'。')
    first=lead+''.join(fragments)
    bank=snapshot.get('central_bank') if isinstance(snapshot.get('central_bank'),dict) else None
    same_day=bool(bank and bank.get('date')==quotes.get('quote_date') and bank.get('term_days') in (1,7))
    title='【流动性指标】质押式回购成交行情'
    second=''
    if same_day:
        product='DR001' if bank['term_days']==1 else 'DR007'
        term='隔夜' if bank['term_days']==1 else '7天期'
        current=rows.get(product) or {}
        if current.get('weighted_rate') not in (None,''):
            direction,amount=_points(current['weighted_rate'],bank['rate_percent'])
            side='高' if direction=='上行' else '低' if direction=='下行' else '持平'
            second=(f"{product}较当日{term}逆回购操作利率{bank['rate_percent']}%"
                    +('持平。' if side=='持平' else f'{side}{amount}个基点。')
                    +f"央行{bank['date']}开展{bank['awarded_yi']}亿元{term}逆回购，"
                    +f"投标量{bank['bid_yi']}亿元，中标量{bank['awarded_yi']}亿元。")
            headline='高于' if side=='高' else '低于' if side=='低' else '持平于'
            title=f"【流动性指标】{product}{headline}{term}逆回购操作利率"+(f'{amount}个基点' if side!='持平' else '')
    elif rows.get('DR001',{}).get('weighted_rate') not in (None,''):
        title=f"【流动性指标】DR001{'盘中' if intraday else '收盘'}加权利率报{rows['DR001']['weighted_rate']}%"
    elif rows.get('DR007',{}).get('weighted_rate') not in (None,''):
        title=f"【流动性指标】DR007{'盘中' if intraday else '收盘'}加权利率报{rows['DR007']['weighted_rate']}%"
    body=first+('\n\n'+second if second else '')
    return {'status':'ok','title':title,'body':body,'approved_titles':[title],'approved_bodies':[body],
            'policy_comparison_included':bool(second)}


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
