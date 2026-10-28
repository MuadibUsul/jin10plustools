"""Team-managed overrides for fixed-brief text Skills.

The code always carries a bundled SKILL.md. A team may enable/disable a skill
and replace its instructions; the effective text is used by the optional
AI wording stage during production in `/briefs/`. Every write keeps
a small immutable history in the team settings JSON.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from app.db.models import utcnow

from .policy import CATEGORIES
from .skill_ai import SKILL_NAMES, asset_dir, sample_label

MAX_INSTRUCTIONS = 8000
MAX_HISTORY = 20
CATEGORY_ORDER = ("volume", "turnover", "valuation", "liquidity", "oil_etf", "token_cost", "gpu_rental")
SKILL_IDS = {
    "volume": "news.fixed-volume",
    "turnover": "news.fixed-turnover",
    "valuation": "news.fixed-valuation",
    "liquidity": "news.fixed-liquidity",
    "oil_etf": "news.fixed-oil-etf",
    "token_cost": "news.fixed-token-cost",
    "gpu_rental": "news.fixed-gpu-rental",
}
CATEGORY_GROUPS = {
    "volume": "A股温度计",
    "turnover": "A股温度计",
    "valuation": "A股温度计",
    "liquidity": "流动性",
    "oil_etf": "原油ETF",
    "token_cost": "AI算力",
    "gpu_rental": "AI算力",
}
class SkillNotFound(ValueError):
    pass


class SkillConflict(ValueError):
    pass


def _bundled(category: str) -> str:
    return (asset_dir(category) / "SKILL.md").read_text(encoding="utf-8")


def _description(text: str) -> str:
    match = re.search(r"(?m)^description:\s*(.+)$", text)
    return match.group(1).strip() if match else ""


def _count_samples(text: str) -> int:
    return sum(1 for line in text.splitlines() if sample_label(line))


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _saved(team) -> dict[str, Any]:
    return dict(((getattr(team, "settings", None) or {}).get("skills") or {}))


def history_samples(category: str) -> list[dict[str, str]]:
    """Parse dated examples from the bundled reference file."""
    text = (asset_dir(category) / "references" / "history.md").read_text(encoding="utf-8")
    samples = []
    current_date = None
    current_title = ""
    body = []
    started = False

    def flush():
        if current_date and body:
            samples.append({"date": current_date, "title": current_title,
                            "body": re.sub(r"\s+", " ", " ".join(body)).strip()})

    for line in text.splitlines():
        label = sample_label(line)
        if line.startswith("### "):
            flush()
            current_date = label
            current_title = ""
            body = []
            started = False
            continue
        if not current_date:
            continue
        if not current_title and line.startswith("- 标题："):
            current_title = line.split("：", 1)[1].strip()
            continue
        if line.strip() == "**动态正文**":
            started = True
            continue
        if started and (line.startswith("**固定说明") or line.strip() == "--------"):
            started = False
            continue
        if started and line.strip():
            body.append(line.strip())
    flush()
    return samples


def effective(team, category: str, *, include_history: bool = False) -> dict[str, Any]:
    if category not in CATEGORY_ORDER:
        raise SkillNotFound("Skill 不存在")
    saved = _saved(team).get(category) or {}
    bundled = _bundled(category)
    instructions = saved.get("instructions") or bundled
    history = list(saved.get("history") or [])
    result = {
        "category": category,
        "label": CATEGORIES[category],
        "group": CATEGORY_GROUPS[category],
        "skill_id": SKILL_IDS[category],
        "version": "0.1.0",
        "enabled": saved.get("enabled", True) is not False,
        "editable": True,
        "kind": "text",
        "customized": bool(saved.get("instructions")),
        "instructions": instructions,
        "bundled_instructions": bundled,
        "description": _description(instructions),
        "digest": _hash(instructions),
        "bundled_digest": _hash(bundled),
        "samples": _count_samples(
            (asset_dir(category) / "references" / "history.md").read_text(encoding="utf-8")
        ),
        "revision": int(saved.get("revision") or 0),
        "updated_at": saved.get("updated_at"),
        "updated_by": saved.get("updated_by"),
        "updated_by_name": saved.get("updated_by_name"),
        "history": [{
            "revision": item.get("revision"),
            "at": item.get("at"),
            "by": item.get("by"),
            "by_name": item.get("by_name"),
            "enabled": item.get("enabled"),
            "customized": bool(item.get("instructions")),
            "digest": _hash(item.get("instructions") or bundled),
        } for item in history],
    }
    if include_history:
        result["history_samples"] = history_samples(category)
    return result


def get_skill(team, category: str, *, include_history: bool = False) -> dict[str, Any]:
    if category in CATEGORY_ORDER:
        return effective(team, category, include_history=include_history)
    raise SkillNotFound("Skill 不存在")


def list_skills(team) -> list[dict[str, Any]]:
    return [effective(team, category) for category in CATEGORY_ORDER]


def update_skill(db, team, user, category: str, *, revision: int, enabled: bool,
                 instructions: str) -> dict[str, Any]:
    if category not in CATEGORY_ORDER:
        raise SkillNotFound("Skill 不存在")
    text = (instructions or "").strip()
    if len(text) > MAX_INSTRUCTIONS:
        raise ValueError(f"Skill 规范最多 {MAX_INSTRUCTIONS} 字")
    saved = _saved(team)
    current = dict(saved.get(category) or {})
    if int(current.get("revision") or 0) != revision:
        raise SkillConflict("Skill 已被其他管理员更新，请刷新后重试")
    new_revision = revision + 1
    stamp = utcnow().isoformat()
    history = list(current.get("history") or [])
    history.append({
        "revision": new_revision,
        "at": stamp,
        "by": str(getattr(user, "id", "")),
        "by_name": getattr(user, "name", ""),
        "enabled": bool(enabled),
        "instructions": text,
    })
    entry = {
        "enabled": bool(enabled),
        "revision": new_revision,
        "updated_at": stamp,
        "updated_by": str(getattr(user, "id", "")),
        "updated_by_name": getattr(user, "name", ""),
        "history": history[-MAX_HISTORY:],
    }
    if text:
        entry["instructions"] = text
    saved[category] = entry
    team.settings = {**(getattr(team, "settings", None) or {}), "skills": saved}
    from app.services.business_history import record
    record(db,team,getattr(user,'id',None))
    db.flush()
    return effective(team, category)
