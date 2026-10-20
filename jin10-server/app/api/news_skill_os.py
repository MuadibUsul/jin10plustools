"""Opt-in, editor-only offline preview; no persistent data or publication."""

from __future__ import annotations

import json
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.briefs.delivery import default_notes
from app.config import get_settings
from app.db.models import BriefConfig
from app.deps import DbSession, Editor

router = APIRouter(prefix="/api/news-skill-os", tags=["news skill os"])
FIXTURES = frozenset({"official", "rumour", "wrong-number"})
FIXED_BRIEFS = {"volume": "news.fixed-volume", "turnover": "news.fixed-turnover",
                "valuation": "news.fixed-valuation", "liquidity": "news.fixed-liquidity",
                "oil_etf": "news.fixed-oil-etf"}


@router.post("/fixtures/{name}/run")
def run_fixture(name: str, user: Editor) -> dict:
    if not get_settings().news_skill_os_enabled:
        raise HTTPException(status_code=404, detail="News Skill OS 未启用")
    if name not in FIXTURES:
        raise HTTPException(status_code=404, detail="样例不存在")
    from app.news_skill_os.manifest import bundled_registry
    from app.news_skill_os.workflow import Workflow, WorkflowEngine

    root = Path(str(files("app.news_skill_os")))
    payload = json.loads((root / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
    registry = bundled_registry()
    workflow = Workflow.load_yaml(root / "manifests" / "research-workflow.yaml", registry)
    result = WorkflowEngine(registry).run(workflow, payload)
    return {"result": asdict(result), "publication_allowed": False,
            "human_approval_required": True, "requested_by": str(user.id)}


@router.post("/fixed-briefs/{category}/preview")
def preview_fixed_brief(category: str, payload: dict, db: DbSession, user: Editor) -> dict:
    """Text-only preview from supplied verified facts and the team's current note."""
    if not get_settings().news_skill_os_enabled:
        raise HTTPException(status_code=404, detail="News Skill OS 未启用")
    if category not in FIXED_BRIEFS:
        raise HTTPException(status_code=404, detail="固定快讯类别不存在")
    if set(payload) != {"data_date", "facts"}:
        raise HTTPException(status_code=422, detail="仅接受 data_date 和 facts；固定解释由团队设置读取")
    from app.news_skill_os.manifest import ManifestError, bundled_registry

    config = db.get(BriefConfig, user.team_id)
    note = (config.notes or {}).get(category, default_notes(category)) if config else default_notes(category)
    skill = bundled_registry().get(FIXED_BRIEFS[category], "0.1.0")
    try:
        result = skill.run({**payload, "fixed_note": note})
    except (ManifestError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"result": result, "skill_id": skill.key[0], "skill_version": skill.key[1],
            "publication_allowed": False, "human_approval_required": True,
            "requested_by": str(user.id)}
