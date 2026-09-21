"""分页：把栏目内的素材排进一页页的正文区域，并按字数自动适配。

规则
- 每个栏目从新的一页开始，首页带栏目头
- 条目标题不孤立在页尾：标题 + 下一段至少 2 行放得下才留在本页
- 段落放不下时按句子拆分到下一页（本页至少放得下 2 行才拆）
- 图片按正文宽度等比缩放；放不下时，若剩余空间足够则缩小放入，否则换页
- 自适应字号：栏目最后一页过空时，尝试 34pt / 32pt，能少占一页则采用（同一栏目字号统一）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PIL import Image

from . import layout, textfit
from .models import FEATURE_SECTIONS, SECTION_LABELS, DailyItem, DailySection, SectionId
from .textfit import Metrics

ParaStyle = Literal["title", "body"]


@dataclass(frozen=True)
class Para:
    style: ParaStyle
    text: str


@dataclass(frozen=True)
class Img:
    path: Path
    aspect: float  # 高 / 宽


Block = Para | Img


@dataclass
class TextFrame:
    top: float
    metrics: Metrics
    paras: list[Para] = field(default_factory=list)

    @property
    def height(self) -> float:
        return textfit.paragraphs_height([p.text for p in self.paras], self.metrics)


@dataclass
class ImageFrame:
    top: float
    left: float
    width: float
    height: float
    path: Path


Frame = TextFrame | ImageFrame


@dataclass
class Page:
    section: SectionId
    section_no: int
    is_section_first: bool
    font_pt: float
    frames: list[Frame] = field(default_factory=list)

    @property
    def top(self) -> float:
        return layout.body_top(self.is_section_first)

    @property
    def cursor(self) -> float:
        """当前内容底部"""
        if not self.frames:
            return self.top
        last = self.frames[-1]
        return last.top + last.height

    @property
    def fill_ratio(self) -> float:
        return (self.cursor - self.top) / (layout.BODY_BOTTOM - self.top)

    @property
    def is_empty(self) -> bool:
        return not self.frames


@dataclass
class PaginationResult:
    pages: list[Page]
    warnings: list[str]


def image_aspect(path: Path) -> float:
    with Image.open(path) as im:
        w, h = im.size
    return h / w


def item_blocks(item: DailyItem, section: SectionId) -> list[Block]:
    blocks: list[Block] = [Para("title", item.title)]
    if section in FEATURE_SECTIONS:
        if item.news:
            blocks.append(Para("body", item.news))
        blocks.extend(Para("body", p) for p in item.interpretation)
    else:
        if item.news:
            blocks.append(Para("body", f"【新闻】{item.news}"))
        for i, p in enumerate(item.interpretation):
            blocks.append(Para("body", f"【解读】{p}" if i == 0 else p))
    blocks.extend(
        Img(img.path, image_aspect(img.path)) for img in item.images[: layout.IMAGE_RULES.max_per_item]
    )
    return blocks


class _SectionPaginator:
    def __init__(self, section: SectionId, section_no: int, metrics: Metrics):
        self.section = section
        self.section_no = section_no
        self.metrics = metrics
        self.warnings: list[str] = []
        self.pages: list[Page] = []
        self._new_page()

    @property
    def page(self) -> Page:
        return self.pages[-1]

    def _new_page(self) -> None:
        self.pages.append(
            Page(self.section, self.section_no, is_section_first=not self.pages, font_pt=self.metrics.font_pt)
        )

    # ---- 文字 ----

    def _text_start(self) -> tuple[float, bool]:
        """下一段文字的起点，以及是否接在已有文本框后"""
        page = self.page
        if page.is_empty:
            return page.top, False
        last = page.frames[-1]
        if isinstance(last, TextFrame):
            return last.top + last.height + self.metrics.paragraph_gap, True
        return last.top + last.height + layout.FRAME_GAP, False

    def _remaining_for_text(self) -> float:
        start, _ = self._text_start()
        return layout.BODY_BOTTOM - start

    def _append_para(self, para: Para) -> None:
        start, continues = self._text_start()
        if continues:
            frame = self.page.frames[-1]
            assert isinstance(frame, TextFrame)
            frame.paras.append(para)
        else:
            self.page.frames.append(TextFrame(top=start, metrics=self.metrics, paras=[para]))

    def _min_lead_height(self, text: str) -> float:
        """段落放在页尾时至少占用的高度：可按句拆分则为前几句（≥2 行），否则为整段"""
        m = self.metrics
        full = textfit.text_height(text, m)
        sentences = textfit.split_sentences(text)
        head = ""
        for s in sentences[:-1]:
            head += s
            if textfit.line_count(head, m) >= 2:
                return textfit.text_height(head, m)
        return full

    def add_para(self, para: Para, next_block: Block | None) -> None:
        m = self.metrics
        height = textfit.text_height(para.text, m)
        needed = height
        if para.style == "title" and isinstance(next_block, Para):
            # 标题不孤立在页尾：下一段的开头必须能跟着放进本页
            needed += m.paragraph_gap + self._min_lead_height(next_block.text)

        if needed <= self._remaining_for_text():
            self._append_para(para)
            return

        if para.style == "body" and self._try_split(para):
            return

        if not self.page.is_empty:
            self._new_page()
        if height > layout.BODY_BOTTOM - self.page.top:
            self.warnings.append(f"段落超出单页：{para.text[:20]}…")
        self._append_para(para)

    def _try_split(self, para: Para) -> bool:
        """按句子把段落拆成两部分：本页放得下的 + 下一页继续"""
        m = self.metrics
        remaining = self._remaining_for_text()
        if remaining < 2 * m.line_height:
            return False
        sentences = textfit.split_sentences(para.text)
        head: list[str] = []
        for s in sentences:
            if textfit.text_height("".join(head + [s]), m) <= remaining:
                head.append(s)
            else:
                break
        if not head or len(head) == len(sentences):
            return False
        head_text = "".join(head)
        if textfit.line_count(head_text, m) < 2:
            return False
        self._append_para(Para("body", head_text))
        self._new_page()
        self.add_para(Para("body", "".join(sentences[len(head):])), None)
        return True

    # ---- 图片 ----

    def add_image(self, img: Img) -> None:
        rules = layout.IMAGE_RULES
        natural = layout.CONTENT_WIDTH * img.aspect
        page = self.page
        start = page.cursor + (layout.FRAME_GAP if not page.is_empty else 0)
        remaining = layout.BODY_BOTTOM - start

        if natural <= remaining:
            height = natural
        elif remaining >= max(rules.min_height, rules.shrink_ratio * natural):
            height = remaining
        else:
            self._new_page()
            start = self.page.top
            capacity = layout.BODY_BOTTOM - start
            height = min(natural, capacity)
            if height < natural:
                self.warnings.append(f"图片过高，已缩小：{img.path.name}")

        width = height / img.aspect
        left = layout.MARGIN_X + (layout.CONTENT_WIDTH - width) / 2
        self.page.frames.append(ImageFrame(top=start, left=left, width=width, height=height, path=img.path))


def _run_section(blocks: list[Block], section: SectionId, section_no: int, metrics: Metrics) -> _SectionPaginator:
    paginator = _SectionPaginator(section, section_no, metrics)
    for i, block in enumerate(blocks):
        next_block = blocks[i + 1] if i + 1 < len(blocks) else None
        if isinstance(block, Para):
            paginator.add_para(block, next_block)
        else:
            paginator.add_image(block)
    return paginator



def paginate_section(section: DailySection, section_no: int) -> tuple[list[Page], list[str]]:
    """按字数自适应：默认 36pt；最后一页过空且更小字号能少占页数时，采用更小字号"""
    blocks = [b for item in section.items for b in item_blocks(item, section.id)]
    best = _run_section(blocks, section.id, section_no, Metrics(layout.FONT_STEPS[0]))

    if len(best.pages) > 1 and best.pages[-1].fill_ratio < layout.SPARSE_LAST_PAGE_RATIO:
        for font in layout.FONT_STEPS[1:]:
            candidate = _run_section(blocks, section.id, section_no, Metrics(font))
            if len(candidate.pages) < len(best.pages):
                best = candidate
                break

    warnings = list(best.warnings)
    if best.metrics.font_pt != layout.FONT_STEPS[0]:
        warnings.append(f"「{SECTION_LABELS[section.id]}」正文自动调整为 {best.metrics.font_pt:g}pt 以减少空白页")
    return best.pages, warnings


def paginate(sections: list[DailySection]) -> PaginationResult:
    warnings: list[str] = []
    pages: list[Page] = []
    for no, section in enumerate(sections, start=1):
        section_pages, section_warnings = paginate_section(section, no)
        pages.extend(section_pages)
        warnings.extend(section_warnings)
    return PaginationResult(pages=pages, warnings=warnings)
