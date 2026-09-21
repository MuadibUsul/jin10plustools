"""PLUS 日报标准版式网格（竖版 15" × 26.67"）。

模板 PPT 为手工制作，各页坐标存在细微偏差；生成时所有元素一律按本文件的网格重新定位，
保证每一页的边距、间距、对齐完全一致。单位：英寸（通过 emu() 转换）。

纵向结构（内容页）：
    0.68 ─ 页眉：PLUS精选标签 · 页眉标题 · 金十数据 Research
    1.37 ─ 页眉分隔线
    1.67 ─ 栏目头「01 | 宏观消息」（仅栏目首页，高 0.95）
    2.90 ─ 正文起点（栏目首页）   / 1.70 ─ 正文起点（续页）
   25.05 ─ 正文终点
   25.35 ─ 免责声明
   25.75 ─ 网址 · 品牌
"""

from __future__ import annotations

from dataclasses import dataclass

EMU_PER_INCH = 914_400


def emu(inches: float) -> int:
    return round(inches * EMU_PER_INCH)


def inches(value_emu: int) -> float:
    return value_emu / EMU_PER_INCH


SLIDE_WIDTH = 15.0
SLIDE_HEIGHT = 24_382_730 / EMU_PER_INCH  # 26.667，与模板一致

MARGIN_X = 1.0
CONTENT_WIDTH = SLIDE_WIDTH - 2 * MARGIN_X  # 13.0
CONTENT_RIGHT = MARGIN_X + CONTENT_WIDTH  # 14.0

# 页眉
HEADER_Y = 0.68
HEADER_H = 0.50
TAG_W = 1.60
RUNNING_TITLE_X = MARGIN_X + TAG_W + 0.15
RUNNING_TITLE_W = 7.0
BRAND_RESEARCH_W = 3.0
HEADER_RULE_Y = 1.37

# 栏目头
SECTION_HEADER_Y = 1.67
SECTION_HEADER_H = 0.95

# 正文区域
BODY_TOP_SECTION = 2.90
BODY_TOP_CONTINUE = 1.70
BODY_BOTTOM = 25.05
FRAME_GAP = 0.50  # 文本与图片之间；含 LibreOffice 行内文字比 PowerPoint 靠下约 0.3" 的余量

# 页脚
FOOTER_DISCLAIMER_Y = 25.35
FOOTER_LINE_Y = 25.75
FOOTER_H = 0.25
FOOTER_BRAND_W = 2.0

# 正文排版（与模板一致：36pt、行距 170%、段后 12pt）
FONT_PT = 36
LINE_HEIGHT = 1.02  # 36pt × 1.7 行距，PowerPoint 实测每行 1.02"
PARAGRAPH_GAP = 12 / 72  # 段后 12pt
EM = FONT_PT / 72  # 一个汉字宽 0.5"

#: 自适应字号：栏目最后一页过空时，依次尝试更小字号，看能否少占一页（最小 32pt，保证手机可读）
FONT_STEPS: tuple[float, ...] = (36, 34, 32)
#: 最后一页内容占比低于该值视为「过空」
SPARSE_LAST_PAGE_RATIO = 0.35


@dataclass(frozen=True)
class ImageRules:
    """图片放置规则"""

    #: 当前页剩余空间 ≥ 该比例 × 图片原高度时，可缩小放入当前页
    shrink_ratio: float = 0.7
    #: 缩小后高度不得低于此值，否则换页
    min_height: float = 5.0
    #: 每条素材最多放几张图
    max_per_item: int = 2


IMAGE_RULES = ImageRules()


def body_top(is_section_first: bool) -> float:
    return BODY_TOP_SECTION if is_section_first else BODY_TOP_CONTINUE


def body_capacity(is_section_first: bool) -> float:
    return BODY_BOTTOM - body_top(is_section_first)
