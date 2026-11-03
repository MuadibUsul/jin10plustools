"""Optional plan selector for a reviewed GPU style pack; returns IDs, not prose."""
from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings
from app.services.team_settings import briefs_ai


def select_plan(team_settings: dict | None, context: dict) -> dict:
    if not get_settings().briefs_skill_ai_enabled:
        return {'status': 'disabled', 'reason': 'disabled'}
    try:
        ai = briefs_ai(SimpleNamespace(settings=dict(team_settings or {})))
    except Exception:
        return {'status': 'failed', 'reason': 'settings_error'}
    if not all(ai.get(key) for key in ('key', 'model', 'base_url')):
        return {'status': 'not_configured', 'reason': 'provider_not_configured'}
    diagnostics = {'provider': ai.get('provider'), 'model': ai.get('model'), 'attempts': 0}
    if ai.get('requested_model'):
        diagnostics['requested_model'] = ai['requested_model']
    story=context.get('schema')=='story_v2'
    options = context.get('options',{})
    messages = [
        {'role': 'system', 'content':
         '你是金十PLUS的固定快讯编辑。按本期栏目、场景和范文的叙述顺序选择表达模块。'
         '事实句由程序填入，不能改写。只返回四个模块ID组成的JSON对象：'
         '{"headline":"ID","lead":"ID","comparison":"ID","closing":"ID"}。'
         '每项必须选择该角色允许的ID；不要返回标题、正文、数据或新判断。'
         + (context.get('writing_order') or '优先突出B200，随后对照H100与A100。')
         + '避免重复总结，closing可选none。'},
        {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)},
    ]
    if story:
        messages[0]['content']=(
            '你是金十PLUS编辑。根据本期事实、团队编辑规范和完整历史范文，决定标题重点、事实句表达及分段。'
            '历史范文只学语气与叙述节奏，绝不能借用旧日期、旧数字或旧结论。'
            '本期事实和判断已经由程序核验，必须从fact_bank的variants中逐字选择；每个required事实恰好使用一次，非required的reading可省略，避免重复总结。'
            '不能返回新句子、数字、原因、预测或投资建议。可调整句序与分段，避免机械重复。'
            '美股市场结构必须以indices事实开头。团队指令仅影响允许范围内的选择，不得覆盖事实边界。'
            'paragraph_rules非空时，必须严格分成指定的两项，不能将两项指标塞进同一项。'
            '只返回JSON：{"headline":"headlines中的ID","paragraphs":[["事实变体ID", "事实变体ID"],["事实变体ID"]]}。'
            'paragraphs最多四段，不得遗漏或重复任何fact_bank事实。')
    for attempt in range(2):
        diagnostics['attempts'] = attempt + 1
        body = {'model': ai['model'], 'stream': False, 'temperature': 0.2,
                'max_tokens': 1000 if story else 400, 'response_format': {'type': 'json_object'}, 'messages': messages}
        if ai.get('provider') == 'deepseek':
            body['thinking'] = {'type': 'disabled'}
        try:
            request = Request(ai['base_url'].rstrip('/') + '/chat/completions',
                              data=json.dumps(body).encode(),
                              headers={'Authorization': 'Bearer ' + ai['key'],
                                       'Content-Type': 'application/json'}, method='POST')
            with urlopen(request, timeout=30) as response:
                raw = response.read(32769)
            if len(raw) > 32768:
                return {**diagnostics, 'status': 'failed', 'reason': 'response_too_large'}
            response = json.loads(raw)
            diagnostics['response_model']=response.get('model')
            diagnostics['usage']={k:v for k,v in (response.get('usage') or {}).items()
                if k in ('prompt_tokens','completion_tokens','total_tokens') and isinstance(v,int)}
            choice = response['choices'][0]
            if choice.get('finish_reason') != 'stop':
                return {**diagnostics, 'status': 'failed', 'reason': 'incomplete_completion'}
            plan = json.loads(choice['message']['content'])
            if story:
                from .sample_story import validate_plan
                validate_plan(context,plan)
                valid=True
            else:valid = (isinstance(plan, dict) and set(plan) == set(options)
                     and all(isinstance(plan[role], str)
                             and plan[role] in {row['id'] for row in rows}
                             for role, rows in options.items()))
            if valid:
                return {**diagnostics, 'status': 'ok', 'plan': plan}
        except HTTPError as exc:
            return {**diagnostics, 'status': 'failed', 'reason': 'provider_http_error',
                    'http_status': exc.code}
        except (TimeoutError, URLError, OSError):
            return {**diagnostics, 'status': 'failed', 'reason': 'network_error'}
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        # One correction at most; no free-form failed response is carried forward.
        messages.append({'role': 'user', 'content': '上次结构、ID或事实覆盖无效。仅返回要求的JSON，不增删事实。'})
    return {**diagnostics, 'status': 'failed', 'reason': 'invalid_plan'}
