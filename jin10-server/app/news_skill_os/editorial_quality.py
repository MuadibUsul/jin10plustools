"""Shared writing guidance and advisory checks for the two news workbenches.

The check never rewrites a draft or labels facts as verified. Source validation
and the editor's review remain separate steps.
"""
from __future__ import annotations

import re

from app.briefs.skill_library import QUALITY_CATEGORY, effective


_FILLER = (
    "总体来看", "综合来看", "值得注意的是", "不难发现", "毋庸置疑",
    "各有利弊", "需要持续关注", "后续需关注",
)


def prompt_for(team) -> str:
    """Return the current team instruction for the drafting model, if enabled."""
    skill = effective(team, QUALITY_CATEGORY)
    if not skill["enabled"]:
        return ""
    return ("\n\n团队文字质量规范（只调整表达，不改变来源事实、栏目格式或字数限制）：\n"
            + skill["instructions"])


def review_text(team, *, target: str, title: str, body: str, notes: str = "",
                unverified: list[str] | None = None) -> dict:
    """Flag reviewable wording issues without changing text or making fact claims."""
    if target not in {"fresh", "annotation"}:
        raise ValueError("文字质量检查仅适用于新鲜事和注解辅助")
    skill = effective(team, QUALITY_CATEGORY)
    result = {"skill_id": skill["skill_id"], "digest": skill["digest"],
              "status": "disabled" if not skill["enabled"] else "no_flags", "issues": []}
    if not skill["enabled"]:
        return result
    # Annotation titles repeat the submitted flash; only generated prose is graded.
    text = (title + "\n" + body) if target == "fresh" else body
    for phrase in _FILLER:
        if phrase in text:
            result["issues"].append(f"文字质量提示：请确认“{phrase}”是否提供了具体信息。")
    if target == "fresh" and unverified and not re.search(r"未核实|待核查|待核实", notes):
        result["issues"].append("文字质量提示：分析中有待核查项，请在备注保留核查边界。")
    if result["issues"]:
        result["status"] = "review"
    return result
