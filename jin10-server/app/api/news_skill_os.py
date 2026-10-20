"""Opt-in, editor-only offline preview; no persistent data or publication."""

from __future__ import annotations

import json
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.deps import Editor

router = APIRouter(prefix="/api/news-skill-os", tags=["news skill os"])
FIXTURES = frozenset({"official", "rumour", "wrong-number"})


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
