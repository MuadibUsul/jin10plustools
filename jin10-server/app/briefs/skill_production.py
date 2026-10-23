"""Mandatory Skill copy stage for the five fixed briefs.

The rule adapter remains the only source of numbers, charts and snapshots.  This
stage asks the team-selected AI to polish the rule title/body with the team's
Skill instructions; any failure (including exhausted AI quota) leaves the
complete fixed-combination rule copy in place and never fails the brief job.
"""
from __future__ import annotations

from types import SimpleNamespace

from .skill_ai import SKILL_NAMES, polish


def apply_skill_copy(payload: dict, category: str, team_settings: dict | None) -> dict:
    if category not in SKILL_NAMES:
        return payload
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
    return payload
