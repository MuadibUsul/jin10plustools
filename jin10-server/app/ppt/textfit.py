"""正文排版测算：估算一段文字在给定宽度、字号下占几行、多高。

服务端（Docker / Linux）没有 PowerPoint 可以实测排版，因此用逐字贪心断行模拟：
- 汉字、全角符号各占 1 em；英文、数字按字符类别取近似宽度
- 英文单词、数字串不可拆分
- 避头尾：闭合标点不能出现在行首，开括号不能出现在行尾
- 标点悬挂：行末闭合标点可挂出边线（与 PowerPoint 默认一致）
参数经 PowerPoint 实测校准：scripts/calibrate_textfit.py（186 段样本，少估为 0）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import layout

# 行首禁止字符（闭合标点）
_NO_LINE_START = set("，。、；：？！）》」』”’】〕…—·,.;:?!)]}%")
# 行尾禁止字符（开括号）
_NO_LINE_END = set("（《「『“‘【〔([{")

#: 每行预留的宽度余量（em），使测算对 PowerPoint 与 LibreOffice 都偏保守
WRAP_SAFETY_EM = 0.35
#: 是否允许行末标点悬挂。生成时关闭（hangingPunct="0"），与 LibreOffice 一致
HANGING_PUNCTUATION = False
#: 标点悬挂：行末的这些标点可挂出边线（PowerPoint 默认行为），最多 1 em
_HANGING = set("，。、；：？！”’）》」』】")

_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.,%+\-/&'’]*")


@dataclass(frozen=True)
class Metrics:
    """某一字号下的排版参数（行距 170%、段后 12pt 随字号等比缩放）"""

    font_pt: float = layout.FONT_PT

    @property
    def scale(self) -> float:
        return self.font_pt / layout.FONT_PT

    @property
    def em(self) -> float:
        return self.font_pt / 72

    @property
    def line_height(self) -> float:
        return layout.LINE_HEIGHT * self.scale

    @property
    def paragraph_gap(self) -> float:
        return layout.PARAGRAPH_GAP * self.scale


DEFAULT_METRICS = Metrics()


def char_width(ch: str) -> float:
    """字符宽度（em），按思源宋体 / 黑体近似；参数经 PowerPoint 实测校准（scripts/calibrate_textfit.py）"""
    if ch.isdigit():
        return 0.50
    if "A" <= ch <= "Z":
        return 0.65
    if "a" <= ch <= "z":
        return 0.46
    if ch == " ":
        return 0.30
    if ch in ".,:;'’":
        return 0.30
    if ch == "%":
        return 0.85
    if ord(ch) < 0x2000:
        return 0.55
    return 1.0


def _tokens(text: str) -> list[str]:
    """切分为不可拆分的排版单元，并按避头尾规则合并"""
    raw: list[str] = []
    i = 0
    while i < len(text):
        m = _LATIN_RUN.match(text, i)
        if m:
            raw.append(m.group(0))
            i = m.end()
        else:
            raw.append(text[i])
            i += 1

    merged: list[str] = []
    carry = ""
    for tok in raw:
        if carry:
            tok = carry + tok
            carry = ""
        if tok in _NO_LINE_END:
            carry = tok
            continue
        if merged and tok[0] in _NO_LINE_START:
            merged[-1] += tok
        else:
            merged.append(tok)
    if carry:
        merged.append(carry)
    return merged


def _width(s: str) -> float:
    return sum(char_width(c) for c in s)


def _hanging_width(tok: str) -> float:
    """单元末尾可悬挂标点的宽度（不占行宽）。
    生成的 PPT 关闭了标点悬挂（LibreOffice 不支持悬挂，关闭后两种渲染器换行一致），故恒为 0。"""
    if not HANGING_PUNCTUATION:
        return 0.0
    k = len(tok)
    while k > 0 and tok[k - 1] in _HANGING:
        k -= 1
    return min(_width(tok[k:]), 1.0)


def wrap(text: str, width_in: float = layout.CONTENT_WIDTH, metrics: Metrics = DEFAULT_METRICS) -> list[str]:
    """模拟断行，返回每一行的文字"""
    # 预留少量余量：刚好顶满行宽的行，LibreOffice（字宽略宽）会提前换行
    capacity = width_in / metrics.em - WRAP_SAFETY_EM
    lines: list[str] = []
    line, used = "", 0.0
    for tok in _tokens(text):
        if line and used + _width(tok) - _hanging_width(tok) > capacity:
            lines.append(line.rstrip())
            line, used, tok = "", 0.0, tok.lstrip()
        line += tok
        used += _width(tok)
        # 单个超长单元（极长英文串）按字符硬切；悬挂标点不计入
        while used - _hanging_width(line) > capacity and len(line) > 1:
            cut = len(line) - 1
            while cut > 1 and _width(line[:cut]) > capacity:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
            used = _width(line)
    if line:
        lines.append(line)
    return lines or [""]


def line_count(text: str, metrics: Metrics = DEFAULT_METRICS, width_in: float = layout.CONTENT_WIDTH) -> int:
    return len(wrap(text, width_in, metrics))


def text_height(text: str, metrics: Metrics = DEFAULT_METRICS, width_in: float = layout.CONTENT_WIDTH) -> float:
    """单段高度（英寸），不含段后间距"""
    return line_count(text, metrics, width_in) * metrics.line_height


def paragraphs_height(
    texts: list[str], metrics: Metrics = DEFAULT_METRICS, width_in: float = layout.CONTENT_WIDTH
) -> float:
    """多段连续文字的总高度（段间有段后间距，最后一段不计）"""
    if not texts:
        return 0.0
    body = sum(text_height(t, metrics, width_in) for t in texts)
    return body + metrics.paragraph_gap * (len(texts) - 1)


_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s]
