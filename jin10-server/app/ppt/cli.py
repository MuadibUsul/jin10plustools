"""命令行生成日报：

    python -m app.ppt.cli samples/2026-09-18/issue.json out/PLUS日报-2026-09-18.pptx

issue.json 中的图片路径相对于该 JSON 文件所在目录。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .generator import generate
from .models import DailyIssue
from .template import DEFAULT_TEMPLATE


def load_issue(path: Path) -> DailyIssue:
    data = json.loads(path.read_text(encoding="utf-8"))
    for section in data.get("sections", []):
        for item in section.get("items", []):
            for img in item.get("images", []):
                img["path"] = str((path.parent / img["path"]).resolve())
    return DailyIssue.model_validate(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 PLUS 日报 PPT")
    parser.add_argument("issue", type=Path, help="日报 JSON")
    parser.add_argument("output", type=Path, help="输出 .pptx")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    args = parser.parse_args()

    report = generate(load_issue(args.issue), args.output, args.template)
    print(f"已生成 {report.output}（{report.page_count} 页）")
    print("栏目：" + " / ".join(report.sections))
    for w in report.warnings:
        print(f"  ⚠ {w}")


if __name__ == "__main__":
    main()
