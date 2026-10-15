import json
import os
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError

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
