"""Offline demonstration: python -m app.news_skill_os --fixture official."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .manifest import bundled_registry
from .workflow import Workflow, WorkflowEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an offline News Skill OS fixture; no network or publishing")
    parser.add_argument("--fixture", choices=("official", "rumour", "wrong-number"), default="official")
    args = parser.parse_args()
    root = Path(str(files("app.news_skill_os")))
    payload = json.loads((root / "fixtures" / f"{args.fixture}.json").read_text(encoding="utf-8"))
    registry = bundled_registry()
    workflow = Workflow.load_yaml(root / "manifests" / "research-workflow.yaml", registry)
    result = WorkflowEngine(registry).run(workflow, payload)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
