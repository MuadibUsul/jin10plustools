"""日报生成输入模型。与插件 src/types/section.ts 的栏目定义保持一致。"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class SectionId(StrEnum):
    MACRO = "macro"
    CN_EQUITY = "cn_equity"
    GLOBAL_EQUITY = "global_equity"
    AI = "ai"
    RESEARCH = "research"
    CHARTS = "charts"


SECTION_LABELS: dict[SectionId, str] = {
    SectionId.MACRO: "宏观消息",
    SectionId.CN_EQUITY: "国内股市动向",
    SectionId.GLOBAL_EQUITY: "海外股市动向",
    SectionId.AI: "AI热点",
    SectionId.RESEARCH: "精选观点",
    SectionId.CHARTS: "精选图表",
}

#: feature 栏目不使用【新闻】【解读】标签，只有标题 + 段落 + 配图
FEATURE_SECTIONS = {SectionId.RESEARCH, SectionId.CHARTS}

SECTION_ORDER = list(SectionId)


class ImageRef(BaseModel):
    """本地图片文件（服务端已下载好的素材图片）"""

    path: Path

    @field_validator("path")
    @classmethod
    def must_exist(cls, value: Path) -> Path:
        if not value.is_file():
            raise ValueError(f"图片不存在：{value}")
        return value


class DailyItem(BaseModel):
    """日报中的一条素材。

    news 栏目：title +【新闻】news +【解读】interpretation[0] + interpretation[1:]
    feature 栏目：title + interpretation（作为普通段落）；news 可选，作为首段
    """

    title: str
    news: str | None = None
    interpretation: list[str] = Field(default_factory=list)
    images: list[ImageRef] = Field(default_factory=list)


class DailySection(BaseModel):
    id: SectionId
    items: list[DailyItem] = Field(default_factory=list)


class DailyIssue(BaseModel):
    date: dt.date
    sections: list[DailySection]
    #: 按 ordered_sections 展开的素材索引；None 时默认取前 12 条
    cover_indices: list[int] | None = Field(default=None, max_length=12)

    def ordered_sections(self) -> list[DailySection]:
        """固定 6 个栏目、固定顺序；没有素材的栏目也保留（生成空白栏目页，供编辑自行补充）"""
        by_id = {s.id: s for s in self.sections}
        return [by_id.get(sid) or DailySection(id=sid) for sid in SECTION_ORDER]

    @property
    def item_count(self) -> int:
        return sum(len(s.items) for s in self.sections)
