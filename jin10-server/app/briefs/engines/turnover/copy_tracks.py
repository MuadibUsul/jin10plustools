"""Independent, standard-library-only turnover copy tracks.

Public interface:
    build_facts(current, previous) -> JSON-serializable validated facts
    generate_rule_copy(facts) -> {status, title, body}
    generate_deepseek_copy(facts, config=None) -> an independent result/status
    generate_copies(facts, config=None) -> {rules, deepseek}

Input rows use date (ISO), low_ma9 and high_ma9, as produced by
build_turnover.load_and_calculate. Only the nine-trading-day averages are
described; raw daily ratios never enter the copy. The API cannot change the
numeric paragraph, and selects an approved title and final sentence from the
fact-dependent alternatives. This deliberately bounds automated publication.

API protocol: https://api-docs.deepseek.com/api/create-chat-completion/
JSON mode: https://api-docs.deepseek.com/guides/json_mode/
No paid request is made unless both DEEPSEEK_API_KEY and DEEPSEEK_MODEL exist.
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_Q = Decimal('0.01')
_MAX_RESPONSE_BYTES = 128 * 1024


def _ratio(value: object) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Expected a finite ratio between 0 and 100') from exc
    if not number.is_finite() or not 0 <= number <= 100:
        raise ValueError('Expected a finite ratio between 0 and 100')
    return number.quantize(_Q, rounding=ROUND_HALF_UP)


def _threshold_phrase(current: Decimal, previous: Decimal) -> str:
    threshold = Decimal(17)
    if current < threshold:
        return '继续低于17%的参考阈值' if previous < threshold else '降至17%的参考阈值下方'
    if current > threshold:
        return '继续高于17%的参考阈值' if previous > threshold else '升至17%的参考阈值上方'
    if previous < threshold:
        return '升至17%的参考阈值'
    if previous > threshold:
        return '降至17%的参考阈值'
    return '维持在17%的参考阈值'


def _movement(current: Decimal, previous: Decimal) -> str:
    verb = '升至' if current > previous else '降至' if current < previous else '持平于'
    return f'{verb}{current:.2f}%'


def build_facts(current: Mapping, previous: Mapping) -> dict:
    """Build the only facts supplied to either copy writer; no I/O occurs.

    Caller must supply adjacent, validated source trading records. Dates are
    checked for ordering here, but this function cannot infer a trade calendar.
    """
    current_date = date.fromisoformat(str(current['date']))
    previous_date = date.fromisoformat(str(previous['date']))
    if previous_date >= current_date:
        raise ValueError('Previous record must predate the current record')
    low = _ratio(current['low_ma9'])
    high = _ratio(current['high_ma9'])
    low_previous = _ratio(previous['low_ma9'])
    high_previous = _ratio(previous['high_ma9'])
    low_delta, high_delta = low - low_previous, high - high_previous
    if low_delta == high_delta == 0:
        trend = 'unchanged'
    elif low_delta >= 0 and high_delta <= 0:
        trend = 'cooling'
    elif low_delta <= 0 and high_delta >= 0:
        trend = 'warming'
    else:
        trend = 'mixed'
    fact_body = (
        f'{current_date.year}年{current_date.month}月{current_date.day}日，'
        f'A股低于3%换手率股票占比的9日均线{_movement(low, low_previous)}，'
        f'高于5%换手率股票占比的9日均线{_movement(high, high_previous)}，'
        f'{_threshold_phrase(high, high_previous)}。'
    )
    return {
        'date': current_date.isoformat(), 'previous_date': previous_date.isoformat(),
        'low_ma9': f'{low:.2f}', 'high_ma9': f'{high:.2f}',
        'previous_low_ma9': f'{low_previous:.2f}',
        'previous_high_ma9': f'{high_previous:.2f}',
        'low_change_pp': f'{low_delta:.2f}', 'high_change_pp': f'{high_delta:.2f}',
        'average_window_trading_days': 9,
        'low_threshold': '96', 'high_threshold': '17',
        'trend': trend, 'fact_body': fact_body,
    }


_ALTERNATIVES = {
    'cooling': {
        'titles': ('【A股温度计】换手率均线显示交易活跃度回落',
                   '【A股温度计】换手率均线呈现降温迹象',
                   '【A股温度计】交易热度有所回落'),
        'evaluations': ('整体来看，市场交易活跃度有所回落。',
                        '从两项占比均线的变化看，市场交易热度有所降温。',
                        '两项占比均线的组合变化，显示市场交易活跃度有所回落。'),
    },
    'warming': {
        'titles': ('【A股温度计】换手率均线显示交易活跃度回升',
                   '【A股温度计】换手率均线呈现升温迹象',
                   '【A股温度计】交易热度有所回升'),
        'evaluations': ('整体来看，市场交易活跃度有所回升。',
                        '从两项占比均线的变化看，市场交易热度有所升温。',
                        '两项占比均线的组合变化，显示市场交易活跃度有所回升。'),
    },
    'unchanged': {
        'titles': ('【A股温度计】两项换手率占比均线持平',
                   '【A股温度计】换手率占比均线暂无变化',
                   '【A股温度计】换手率占比均线保持稳定'),
        'evaluations': ('两项占比均线均持平，交易活跃度暂无明显变化。',
                        '从两项占比均线看，市场交易活跃度保持平稳。',
                        '两项占比均线未变，市场交易热度暂未出现明显变化。'),
    },
    'mixed': {
        'titles': ('【A股温度计】换手率占比均线变化有所分化',
                   '【A股温度计】两项换手率均线释放不同信号',
                   '【A股温度计】交易活跃度变化仍待观察'),
        'evaluations': ('两项占比均线对交易活跃度的指示有所分化，后续变化仍需观察。',
                        '两项占比均线未形成一致的升温或降温信号，交易活跃度变化仍需观察。',
                        '从两项占比均线看，市场交易热度的变化方向尚不一致。'),
    },
}


def _checked_facts(facts: Mapping) -> dict:
    # Recompute rather than trust externally edited trend/paragraph/delta fields.
    rebuilt = build_facts(
        {'date': facts['date'], 'low_ma9': facts['low_ma9'], 'high_ma9': facts['high_ma9']},
        {'date': facts['previous_date'], 'low_ma9': facts['previous_low_ma9'],
         'high_ma9': facts['previous_high_ma9']},
    )
    for key, value in rebuilt.items():
        if key in facts and facts[key] != value:
            raise ValueError(f'Inconsistent calculated fact: {key}')
    return rebuilt


def generate_rule_copy(facts: Mapping) -> dict:
    """Always-available deterministic complete copy, with no network or AI."""
    facts = _checked_facts(facts)
    alternatives = _ALTERNATIVES[facts['trend']]
    return {'status': 'ok', 'title': alternatives['titles'][0],
            'body': facts['fact_body'] + alternatives['evaluations'][0]}


@dataclass(frozen=True)
class DeepSeekConfig:
    """One bounded request per invocation; no automatic paid retries.

    Set model explicitly from the provider's current available models. The API
    key is excluded from repr and never included in a returned result/log.
    """
    api_key: str = field(default='', repr=False)
    model: str = ''
    base_url: str = 'https://api.deepseek.com'
    timeout_seconds: float = 30
    max_tokens: int = 768

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> 'DeepSeekConfig':
        env = os.environ if env is None else env
        return cls(api_key=env.get('DEEPSEEK_API_KEY', '').strip(),
                   model=env.get('DEEPSEEK_MODEL', '').strip(),
                   base_url=env.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').strip(),
                   timeout_seconds=float(env.get('DEEPSEEK_TIMEOUT_SECONDS', '30')),
                   max_tokens=int(env.get('DEEPSEEK_MAX_TOKENS', '768')))


def _validate_config(config: DeepSeekConfig) -> str:
    parsed = urlparse(config.base_url)
    if (parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('DeepSeek base URL must be an HTTPS URL without credentials/query/fragment')
    if not 1 <= config.timeout_seconds <= 120:
        raise ValueError('DeepSeek timeout must be between 1 and 120 seconds')
    if type(config.max_tokens) is not int or not 64 <= config.max_tokens <= 4096:
        raise ValueError('DeepSeek max_tokens must be between 64 and 4096')
    return config.base_url.rstrip('/') + '/chat/completions'


def validate_ai_copy(candidate: object, facts: Mapping) -> dict:
    """Reject altered numeric facts and unrestricted claims, not just new digits.

    Whitelisting the whole paragraph also rejects Chinese-number additions,
    swapped indicator values, false threshold events and invented forecasts.
    """
    facts = _checked_facts(facts)
    if not isinstance(candidate, dict) or set(candidate) != {'title', 'body'}:
        raise ValueError('AI copy must contain exactly title and body')
    alternatives = _ALTERNATIVES[facts['trend']]
    title, body = candidate['title'], candidate['body']
    if not isinstance(title, str) or title not in alternatives['titles']:
        raise ValueError('AI title is outside the fact-approved alternatives')
    allowed_bodies = {facts['fact_body'] + text for text in alternatives['evaluations']}
    if not isinstance(body, str) or body not in allowed_bodies:
        raise ValueError('AI body changes facts or adds an unapproved claim')
    return {'title': title, 'body': body}


def generate_deepseek_copy(facts: Mapping, config: DeepSeekConfig | None = None,
                           *, opener: Callable | None = None) -> dict:
    """Run the independent API track; failures return status, never hide rules.

    opener is an injectable urlopen-compatible callable for offline tests.
    The function performs no retries and never returns raw exception/provider
    text, because it may contain credentials, request headers or private data.
    The caller should save rule output before invoking this optional track.
    """
    facts = _checked_facts(facts)
    try:
        config = config or DeepSeekConfig.from_env()
        if not config.api_key or not config.model:
            missing = [name for name, value in (
                ('DEEPSEEK_API_KEY', config.api_key), ('DEEPSEEK_MODEL', config.model)) if not value]
            return {'status': 'not_configured', 'missing': missing}
        endpoint = _validate_config(config)
    except (TypeError, ValueError):
        return {'status': 'configuration_error', 'error': 'invalid_deepseek_configuration'}

    alternatives = _ALTERNATIVES[facts['trend']]
    prompt = {
        'facts': facts,
        'allowed_titles': alternatives['titles'],
        'allowed_evaluations': alternatives['evaluations'],
        'preferred_rule_title': alternatives['titles'][0],
        'instruction': ('请选择与事实相符、适合财经快讯的一个标题和一句评价，优先使用与规则稿不同的表达。'
                        'title必须原样取自allowed_titles；body必须为fact_body原文紧接一条allowed_evaluations。'
                        '不得增删事实段、添加数字、解释原因、预测行情或提供投资建议。'),
    }
    payload = {'model': config.model, 'stream': False,
               'max_tokens': config.max_tokens,
               'response_format': {'type': 'json_object'},
               'messages': [
                   {'role': 'system', 'content': ('你是受约束的财经文字编辑。仅按用户提供的已审定事实与表达选项生成json。'
                                                 '只输出两个字符串字段，格式：{"title":"标题","body":"正文"}。')},
                   {'role': 'user', 'content': json.dumps(prompt, ensure_ascii=False)},
               ]}
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      headers={'Authorization': 'Bearer ' + config.api_key,
                               'Content-Type': 'application/json'}, method='POST')
    opener = opener or urlopen
    try:
        with opener(request, timeout=config.timeout_seconds) as response:
            data = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(data) > _MAX_RESPONSE_BYTES:
            return {'status': 'invalid_response', 'error': 'response_too_large'}
        envelope = json.loads(data)
        choice = envelope['choices'][0]
        if choice.get('finish_reason') != 'stop':
            return {'status': 'invalid_response', 'error': 'completion_not_finished'}
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            return {'status': 'invalid_response', 'error': 'empty_content'}
        try:
            copy = validate_ai_copy(json.loads(content), facts)
        except (ValueError, TypeError):
            return {'status': 'validation_failed', 'error': 'copy_does_not_match_approved_facts'}
        return {'status': 'ok', **copy, 'model': config.model}
    except HTTPError as exc:
        return {'status': 'api_error', 'http_status': exc.code}
    except (TimeoutError, socket.timeout):
        return {'status': 'timeout'}
    except URLError as exc:
        return {'status': 'timeout' if isinstance(exc.reason, (TimeoutError, socket.timeout))
                else 'network_error'}
    except (ValueError, KeyError, IndexError, TypeError, UnicodeError):
        return {'status': 'invalid_response', 'error': 'malformed_provider_response'}
    except OSError:
        return {'status': 'network_error'}


def generate_copies(facts: Mapping, config: DeepSeekConfig | None = None,
                    *, opener: Callable | None = None) -> dict:
    """Convenience wrapper; for immediate persistence save rules first instead."""
    return {'rules': generate_rule_copy(facts),
            'deepseek': generate_deepseek_copy(facts, config, opener=opener)}
