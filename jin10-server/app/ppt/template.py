"""标准模板约定。

标准模板（templates/plus_daily_template.pptx）由 prepare_template.py 从手工 PPT 整理得到，含 3 张原型页，
以幻灯片名称（cSld name）识别；生成器只依赖下列形状名称。设计师修改模板时保持名称不变即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pptx import Presentation
from pptx.presentation import Presentation as PresentationType
from pptx.slide import Slide

from .pptx_xml import find_shape, paragraphs, qn

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
DEFAULT_TEMPLATE = TEMPLATE_DIR / "plus_daily_template.pptx"

SLIDE_COVER = "cover"
SLIDE_SECTION = "page-section"
SLIDE_CONTINUE = "page-continue"


class Shapes:
    # 封面
    COVER_DATE = "cover-date"
    COVER_TOC = "cover-toc"
    COVER_TITLE = "cover-title"
    COVER_TAG = "cover-tag"
    # 内容页
    PLUS_TAG = "plus-tag"
    RUNNING_TITLE = "running-title"
    HEADER_RULE = "header-rule"
    BRAND_RESEARCH = "brand-research"
    SECTION_HEADER = "section-header"
    SECTION_NO = "section-no"
    SECTION_TITLE = "section-title"
    BODY = "body"
    FOOTER_DISCLAIMER = "footer-disclaimer"
    FOOTER_URL = "footer-url"
    FOOTER_BRAND = "footer-brand"


PAGE_CHROME = [
    Shapes.PLUS_TAG,
    Shapes.RUNNING_TITLE,
    Shapes.HEADER_RULE,
    Shapes.BRAND_RESEARCH,
    Shapes.BODY,
    Shapes.FOOTER_DISCLAIMER,
    Shapes.FOOTER_URL,
    Shapes.FOOTER_BRAND,
]

REQUIRED: dict[str, list[str]] = {
    SLIDE_COVER: [Shapes.COVER_DATE, Shapes.COVER_TOC],
    SLIDE_SECTION: [*PAGE_CHROME, Shapes.SECTION_HEADER, Shapes.SECTION_NO, Shapes.SECTION_TITLE],
    SLIDE_CONTINUE: PAGE_CHROME,
}


def slide_name(slide: Slide) -> str:
    return slide._element.find(qn("p:cSld")).get("name", "")


def set_slide_name(slide: Slide, name: str) -> None:
    slide._element.find(qn("p:cSld")).set("name", name)


@dataclass
class LoadedTemplate:
    prs: PresentationType
    cover: Slide
    section: Slide
    cont: Slide

    @property
    def prototypes(self) -> list[Slide]:
        return [self.cover, self.section, self.cont]


def load_template(path: Path = DEFAULT_TEMPLATE) -> LoadedTemplate:
    """加载并校验标准模板，缺少约定形状时给出明确错误"""
    prs = Presentation(str(path))
    by_name = {slide_name(s): s for s in prs.slides}
    missing: list[str] = []
    for sname, shapes in REQUIRED.items():
        slide = by_name.get(sname)
        if slide is None:
            missing.append(f"幻灯片「{sname}」")
            continue
        missing += [f"{sname} / {n}" for n in shapes if find_shape(slide, n) is None]
    if missing:
        raise ValueError("模板不符合约定，缺少：" + "、".join(missing))

    for sname in (SLIDE_SECTION, SLIDE_CONTINUE):
        body = find_shape(by_name[sname], Shapes.BODY)
        assert body is not None
        if len(paragraphs(body)) < 2:
            raise ValueError(f"{sname} 的 body 需要 2 个样式段落（标题样式、正文样式）")

    return LoadedTemplate(prs, by_name[SLIDE_COVER], by_name[SLIDE_SECTION], by_name[SLIDE_CONTINUE])
