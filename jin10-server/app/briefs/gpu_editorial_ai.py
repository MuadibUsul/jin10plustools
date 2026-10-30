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
    options = context['options']
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
    for attempt in range(2):
        diagnostics['attempts'] = attempt + 1
        body = {'model': ai['model'], 'stream': False, 'temperature': 0.2,
                'max_tokens': 400, 'response_format': {'type': 'json_object'}, 'messages': messages}
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
            choice = response['choices'][0]
            if choice.get('finish_reason') != 'stop':
                return {**diagnostics, 'status': 'failed', 'reason': 'incomplete_completion'}
            plan = json.loads(choice['message']['content'])
            valid = (isinstance(plan, dict) and set(plan) == set(options)
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
        messages.append({'role': 'user', 'content': '上次结构或ID无效。只选允许的四个ID，返回四键JSON。'})
    return {**diagnostics, 'status': 'failed', 'reason': 'invalid_plan'}
