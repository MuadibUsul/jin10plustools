"""Shared OpenAI-compatible JSON completion transport for fixed-brief AI helpers.

Callers pass their module-level ``urlopen`` via ``opener`` so test suites that
monkeypatch ``urlopen`` on individual modules continue to work unchanged.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .engines.liquidity.copy_tracks import parse_answer


def call_json_completion(
    ai: dict[str, Any],
    payload: dict[str, Any],
    *,
    opener=urlopen,
    timeout: int = 30,
    max_bytes: int = 32_768,
    truncate_reason: str = 'incomplete_completion',
) -> dict[str, Any]:
    body = dict(payload)
    if ai.get('provider') == 'deepseek':
        body.setdefault('thinking', {'type': 'disabled'})
    diagnostics: dict[str, Any] = {
        'provider': ai.get('provider'),
        'model': ai.get('model'),
    }
    if ai.get('requested_model'):
        diagnostics['requested_model'] = ai['requested_model']
    try:
        request = Request(
            str(ai['base_url']).rstrip('/') + '/chat/completions',
            data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
            headers={
                'Authorization': 'Bearer ' + str(ai['key']),
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        with opener(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return {**diagnostics, 'status': 'failed', 'reason': 'response_too_large'}
        data = json.loads(raw)
        if data.get('model') is not None:
            diagnostics['response_model'] = data.get('model')
        diagnostics['usage'] = {
            key: value
            for key, value in (data.get('usage') or {}).items()
            if key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
            and isinstance(value, int) and not isinstance(value, bool)
        }
        choice = data['choices'][0]
        finish_reason = choice.get('finish_reason')
        diagnostics['finish_reason'] = finish_reason
        if finish_reason != 'stop':
            reason = truncate_reason if finish_reason == 'length' else 'incomplete_completion'
            return {**diagnostics, 'status': 'failed', 'reason': reason}
        content = (choice.get('message') or {}).get('content')
        if not isinstance(content, str) or not content.strip():
            return {**diagnostics, 'status': 'failed', 'reason': 'empty_content'}
        return {**diagnostics, 'status': 'ok', 'parsed': parse_answer(content)}
    except HTTPError as exc:
        return {**diagnostics, 'status': 'failed', 'reason': 'provider_http_error', 'http_status': exc.code}
    except (TimeoutError, URLError, OSError):
        return {**diagnostics, 'status': 'failed', 'reason': 'network_error'}
    except (ValueError, KeyError, IndexError, TypeError):
        return {**diagnostics, 'status': 'failed', 'reason': 'invalid_response'}
