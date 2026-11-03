"""Mandatory Skill copy stage for the fixed briefs.

The rule adapter remains the only source of numbers, charts and snapshots.  The
two AI-compute categories first apply the corpus style templates; the optional
model may then polish only inside the number guard.  Any AI failure leaves that
styled rule copy in place and never fails the brief job.
"""
from __future__ import annotations

from types import SimpleNamespace

from .skill_ai import SKILL_NAMES, polish


def apply_skill_copy(payload: dict, category: str, team_settings: dict | None) -> dict:
    from app.config import get_settings
    from .editorial_authorization import effective_mode
    from .editorial_suite import apply_editorial
    if effective_mode(team_settings,get_settings().briefs_editorial_mode)=='active':
        baseline={'title':payload['title'],'body':payload['dynamic_body']}
        previous_trace=payload.get('editorial')
        apply_editorial(payload,category,team_settings)
        trace=payload.get('editorial') or {}
        if trace is not previous_trace and trace.get('status')=='active':
            payload['rule_copy']=baseline
            selector=trace.get('selector') or {}
            payload['skill']={**selector,'mode':'bounded_editorial','style_status':'active',
                'selection_status':trace.get('selection_status')}
            payload['warnings']=[w for w in payload.get('warnings',[]) if not
                w.startswith(('AI 额度不足，已自动回退固定组合文案','Skill AI 未完成（'))]
            if selector.get('http_status') in (402,429):
                payload['warnings'].append('AI额度不足，已采用样本风格固定组合文案')
            elif trace.get('selection_status')=='deterministic_fallback':
                payload['warnings'].append('AI表达选择未完成，已采用样本风格固定组合文案')
            if category in ('token_cost','gpu_rental','liquidity'):
                from .delivery import complete_body
                payload['dynamic_body']=complete_body(category,payload['dynamic_body'],payload.get('fixed_notes'))
            return payload
    if category not in SKILL_NAMES:
        return apply_editorial(payload, category, team_settings)
    if category in ('token_cost', 'gpu_rental') and not payload.get('body_template_custom'):
        from .ai_compute_style import apply_compute_style
        apply_compute_style(payload, category)
    payload['rule_copy']={'title':payload['title'],'body':payload['dynamic_body']}
    try:
        result = polish(SimpleNamespace(settings=dict(team_settings or {})), category,
                        payload["title"], payload["dynamic_body"])
    except Exception:
        result = {"status": "failed", "reason": "unexpected_error"}
    payload["skill"] = result
    if result.get("status") == "ok":
        payload["title"] = result["title"]
        payload["dynamic_body"] = result["body"]
    else:
        if result.get("http_status") in (402, 429) or result.get("reason") in ("quota_exhausted", "rate_limited"):
            warning = "AI 额度不足，已自动回退固定组合文案"
        else:
            warning = f"Skill AI 未完成（{result.get('reason') or 'unknown'}），已自动回退固定组合文案"
        payload.setdefault("warnings", []).append(warning)
    if category in ("token_cost", "gpu_rental", "liquidity"):
        from .delivery import complete_body
        # Notes stay on the chart footer. Drop them if a model pasted the block into the body.
        payload["dynamic_body"] = complete_body(category, payload["dynamic_body"], payload.get("fixed_notes"))
    from .editorial_suite import apply_editorial
    apply_editorial(payload, category, team_settings)
    return payload
