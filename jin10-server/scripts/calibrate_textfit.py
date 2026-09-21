"""校准正文测算：生成校准 PPT → PowerPoint 实测每段行数 → 对比估算。

    python scripts/calibrate_textfit.py build out/calibration     # 生成校准 PPT 与段落清单
    powershell -File scripts/measure_lines.ps1 out/calibration     # 实测（开发机，需要 PowerPoint）
    python scripts/calibrate_textfit.py compare out/calibration   # 对比估算与实测

每段单独一个文本框，覆盖三种宽度与三档字号，标题与正文样式各测一遍。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from app.ppt import layout, textfit
from app.ppt.generator import _render_text_frame
from app.ppt.paginate import Para, TextFrame
from app.ppt.pptx_xml import clone_slide, delete_slide, paragraphs, remove, require_shape, set_geometry
from app.ppt.template import Shapes, load_template
from app.ppt.textfit import Metrics

SAMPLE = Path("samples/2026-09-18/issue.json")
WIDTHS = [layout.CONTENT_WIDTH, 11.0, 8.5]
FONTS = [36, 32]


def texts() -> list[tuple[str, str]]:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for s in data["sections"]:
        for it in s["items"]:
            out.append(("title", it["title"]))
            if it["news"]:
                out.append(("body", "【新闻】" + it["news"]))
            out += [("body", ("【解读】" if i == 0 else "") + p) for i, p in enumerate(it["interpretation"])]
    return out


def build(out_dir: Path) -> None:
    tpl = load_template()
    cases = []
    per_slide = 4
    items = [(style, text, w, f) for style, text in texts() for w in WIDTHS for f in FONTS]
    for start in range(0, len(items), per_slide):
        slide = clone_slide(tpl.prs, tpl.cont)
        proto = require_shape(slide, Shapes.BODY)
        ps = paragraphs(proto)
        title_p, body_p, proto_copy = copy.deepcopy(ps[0]), copy.deepcopy(ps[1]), copy.deepcopy(proto)
        remove(proto)
        top = layout.BODY_TOP_CONTINUE
        for k, (style, text, w, f) in enumerate(items[start : start + per_slide]):
            frame = TextFrame(top=top, metrics=Metrics(f), paras=[Para(style, text)])  # type: ignore[arg-type]
            _render_text_frame(slide, frame, proto_copy, title_p, body_p, 1000 + k)
            sp = slide.shapes._spTree[-1]
            set_geometry(sp, w=w, h=0.5)
            cases.append({"slide": len(cases) // per_slide + 1, "shape": f"body-{1000 + k}", "style": style, "text": text, "width": w, "font": f})
            top += 5.5
    for proto in tpl.prototypes:
        delete_slide(tpl.prs, proto)
    out_dir.mkdir(parents=True, exist_ok=True)
    tpl.prs.save(str(out_dir / "calibration.pptx"))
    (out_dir / "cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cases)} 个测试段落")


def compare(out_dir: Path) -> None:
    cases = json.loads((out_dir / "cases.json").read_text(encoding="utf-8"))
    measured = json.loads((out_dir / "measured.json").read_text(encoding="utf-8-sig"))
    by_key = {(m["slide"], m["shape"]): m["lines"] for m in measured}
    ok = over = under = 0
    for c in cases:
        actual = by_key[(c["slide"], c["shape"])]
        est = textfit.line_count(c["text"], Metrics(c["font"]), c["width"])
        if est == actual:
            ok += 1
        elif est > actual:
            over += 1
            print(f"  多估 {est}>{actual} w={c['width']} {c['font']}pt {c['text'][:24]}")
        else:
            under += 1
            print(f"  少估 {est}<{actual} w={c['width']} {c['font']}pt {c['text'][:24]}")
    print(f"一致 {ok} / {len(cases)}，多估 {over}，少估 {under}")


if __name__ == "__main__":
    {"build": build, "compare": compare}[sys.argv[1]](Path(sys.argv[2]))
