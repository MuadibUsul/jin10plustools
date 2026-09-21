"""把手工制作的日报 PPT 整理为标准模板。

    python -m app.ppt.prepare_template templates/plus_daily_source.pptx templates/plus_daily_template.pptx

处理内容
- 只保留 3 张原型页：封面（第 1 页）、栏目首页（第 2 页）、续页（第 3 页）
- 形状改为语义化名称（见 template.Shapes），生成器按名称定位
- 删除设计备注、续页中的示例图片
- 正文框只保留两个样式段落：标题样式、正文样式
- 封面左侧文字统一对齐到同一条竖线，去掉文本框内边距造成的错位
"""

from __future__ import annotations

import sys
from pathlib import Path

from pptx import Presentation

from . import layout
from .pptx_xml import (
    delete_slide,
    find_shape,
    paragraphs,
    remove,
    rename,
    require_shape,
    set_geometry,
    set_insets,
    set_paragraph_text,
)
from .template import SLIDE_CONTINUE, SLIDE_COVER, SLIDE_SECTION, Shapes, set_slide_name

#: 封面左侧文字对齐线（英寸）
COVER_LEFT = 1.33

# 手工 PPT 中的形状名称 → 标准名称
_COVER_RENAMES = {
    "矩形 8": Shapes.COVER_DATE,
    "文本框 45": Shapes.COVER_TOC,
    "矩形 6": Shapes.COVER_TITLE,
    "圆角矩形 3": Shapes.COVER_TAG,
    "文本框 43": Shapes.BRAND_RESEARCH,
}
_PAGE_RENAMES = {
    "圆角矩形 3": Shapes.PLUS_TAG,
    "直接连接符 6": Shapes.HEADER_RULE,
    "文本框 43": Shapes.BRAND_RESEARCH,
    "组合 5": Shapes.SECTION_HEADER,
    "文本框 1": Shapes.BODY,
}
_COVER_REMOVE = ["矩形 30"]  # 设计备注「可编辑文本 / 竖版日报尺寸 / 浅蓝会员配色」
_CONTINUE_REMOVE = ["图片 5"]  # 示例图片

_SAMPLE_TITLE = "条目标题（标题样式）"
_SAMPLE_BODY = "【新闻】正文段落（正文样式）"


def _apply_renames(slide, renames: dict[str, str]) -> None:
    for old, new in renames.items():
        el = find_shape(slide, old)
        if el is not None:
            rename(el, new)


def _keep_style_paragraphs(slide) -> None:
    body = require_shape(slide, Shapes.BODY)
    ps = paragraphs(body)
    title_p, body_p = ps[0], ps[1]
    set_paragraph_text(title_p, _SAMPLE_TITLE)
    set_paragraph_text(body_p, _SAMPLE_BODY)
    for p in ps[2:]:
        p.getparent().remove(p)


def _align_cover(slide) -> None:
    for name in (Shapes.COVER_TITLE, Shapes.COVER_DATE, Shapes.COVER_TOC, Shapes.BRAND_RESEARCH):
        el = require_shape(slide, name)
        set_geometry(el, x=COVER_LEFT)
        set_insets(el, left=0)
    set_geometry(require_shape(slide, Shapes.COVER_TAG), x=COVER_LEFT)
    for name in (Shapes.FOOTER_URL, Shapes.FOOTER_BRAND):
        el = find_shape(slide, name)
        if el is not None:
            set_geometry(el, y=layout.FOOTER_LINE_Y, h=layout.FOOTER_H)


def prepare(source: Path, target: Path) -> None:
    prs = Presentation(str(source))
    slides = list(prs.slides)
    if len(slides) < 3:
        raise ValueError("源 PPT 至少需要 3 页：封面、栏目首页、续页")
    cover, section, cont = slides[0], slides[1], slides[2]

    for s in slides[3:]:
        delete_slide(prs, s)

    set_slide_name(cover, SLIDE_COVER)
    set_slide_name(section, SLIDE_SECTION)
    set_slide_name(cont, SLIDE_CONTINUE)

    _apply_renames(cover, _COVER_RENAMES)
    for name in _COVER_REMOVE:
        el = find_shape(cover, name)
        if el is not None:
            remove(el)
    _align_cover(cover)

    for slide in (section, cont):
        _apply_renames(slide, _PAGE_RENAMES)
        _keep_style_paragraphs(slide)
    for name in _CONTINUE_REMOVE:
        el = find_shape(cont, name)
        if el is not None:
            remove(el)

    target.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(target))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"标准模板已生成：{sys.argv[2]}")
