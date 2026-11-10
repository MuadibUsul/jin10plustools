"""Provider transport for a turnover draft with program-bound facts."""
from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings
from app.services.team_settings import briefs_ai

from .engines.liquidity.copy_tracks import parse_answer


_CONTEXT_FIELDS = ('facts', 'required_body_tokens', 'examples', 'instructions', 'data_date')
_OPTIONAL_CONTEXT_FIELDS = ('token_semantics',)
_MAX_RESPONSE_BYTES = 32_768


def system_prompt(context):
    """Name only the tokens this draft can actually render.

    The source facts still use low_ma9 and high_ma9. Those names are not draft
    tokens; telling the model to emit them makes every completion fail validation.
    """
    facts = context.get('facts') or {}
    required = [name for name in context.get('required_body_tokens') or [] if name in facts]
    if not required or set(required) != set(context.get('required_body_tokens') or []):
        raise ValueError('turnover_draft_prompt_tokens')
    required_text = '、'.join(f'{{{{{name}}}}}' for name in required)
    allowed = '、'.join(f'{{{{{name}}}}}' for name in facts)
    return (
        '你是金十 PLUS「A股温度计·换手率」编辑。根据本期最值得写的一处变化，'
        '分别写一条标题和一段短快讯。让句序随看点变化，自己写句子和衔接。'
        '范文只供学习写法，不得使用其中的日期、数字、事件或结论。'
        'facts 提供可插入句中的日期或事实片段。引用这些值时，原样使用对应的 {{token}}，'
        '不要手写数据或重复它已带的单位。token_semantics 说明每个占位符的含义，表达必须与它一致。'
        f'正文中这些占位符各出现恰好一次：{required_text}。'
        f'只能使用已提供的占位符：{allowed}。不要使用 facts 中没有的名字。'
        '标题不要使用 {{date}}，也不要把同一事实的长短两种占位符同时放进标题。'
        '标题至少使用一个已提供的占位符，可以复用正文中的占位符。'
        '正文以替换后的长度为准，尽量控制在110字以内。先把看点写清，'
        '无需另起多余段落或写总结。'
        '不要编造原因、行情、预测或投资建议，不要拼入固定注释或导流语。'
        '团队指令只能指导写作风格，不得修改以上事实边界和输出格式。'
        '标题只写一行，省略【A股温度计】栏目前缀。'
        '只返回 JSON 对象，且仅含两个字符串字段：{"title":"...","body":"..."}。'
    )


def request_draft(team_settings: dict | None, context: dict) -> dict:
    """Request one untrusted draft. The caller validates and renders its tokens."""
    if not get_settings().briefs_skill_ai_enabled:
        return {'status': 'disabled', 'reason': 'disabled'}
    try:
        ai = briefs_ai(SimpleNamespace(settings=dict(team_settings or {})))
    except Exception:
        return {'status': 'failed', 'reason': 'settings_error'}
    if not all(ai.get(field) for field in ('key', 'model', 'base_url')):
        return {'status': 'not_configured', 'reason': 'provider_not_configured'}

    diagnostics = {'provider': ai.get('provider'), 'model': ai['model']}
    try:
        evidence = {field: context[field] for field in _CONTEXT_FIELDS}
        evidence.update({field: context[field] for field in _OPTIONAL_CONTEXT_FIELDS if field in context})
        content = json.dumps(evidence, ensure_ascii=False)
        prompt = system_prompt(context)
    except (TypeError, KeyError, ValueError):
        return {**diagnostics, 'status': 'failed', 'reason': 'invalid_context'}
    payload = {
        'model': ai['model'], 'stream': False, 'temperature': 0.35, 'max_tokens': 1000,
        'response_format': {'type': 'json_object'},
        'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': content}],
    }
    if ai.get('provider') == 'deepseek':
        payload['thinking'] = {'type': 'disabled'}

    try:
        request = Request(ai['base_url'].rstrip('/') + '/chat/completions', method='POST',
                          headers={'Content-Type': 'application/json'},
                          data=json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        request.add_unredirected_header('Authorization', 'Bearer ' + ai['key'])
        with urlopen(request, timeout=30) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            return {**diagnostics, 'status': 'failed', 'reason': 'response_too_large'}
        choice = json.loads(raw)['choices'][0]
        if choice['finish_reason'] != 'stop':
            return {**diagnostics, 'status': 'failed', 'reason': 'incomplete_completion'}
        answer = choice['message']['content']
        if not isinstance(answer, str) or not answer.strip():
            return {**diagnostics, 'status': 'failed', 'reason': 'empty_content'}
        draft = parse_answer(answer)
        if (not isinstance(draft, dict) or set(draft) != {'title', 'body'}
                or not all(isinstance(value, str) and value.strip() for value in draft.values())):
            raise ValueError('invalid_draft_shape')
        return {**diagnostics, 'status': 'ok', 'draft': draft}
    except HTTPError as exc:
        return {**diagnostics, 'status': 'failed', 'reason': 'provider_http_error', 'http_status': exc.code}
    except (TimeoutError, URLError, OSError):
        return {**diagnostics, 'status': 'failed', 'reason': 'network_error'}
    except (ValueError, KeyError, IndexError, TypeError):
        return {**diagnostics, 'status': 'failed', 'reason': 'invalid_response'}
