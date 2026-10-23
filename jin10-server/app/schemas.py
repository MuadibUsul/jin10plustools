"""接口数据结构。字段名与插件 NewsMaterial 保持一致（snake_case）。"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ItemKind = Literal["flash", "vip", "article"]
CollectMethod = Literal["context_menu", "page_scan"]
InterpretationStatus = Literal["none", "pending", "fetched", "no_permission", "failed", "manual"]
ImageSource = Literal["flash", "plus", "upload"]


# ---------- 认证 ----------


class LoginRequest(BaseModel):
    #: 不做严格邮箱格式校验：公司内网域名（如 .local）也需要能登录
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: str
    team_id: uuid.UUID


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: dt.datetime
    user: UserOut
    team: TeamOut


class MeResponse(BaseModel):
    user: UserOut
    team: TeamOut
    members: list[UserOut]


# ---------- 素材 ----------


class MaterialImage(BaseModel):
    url: str
    source: ImageSource
    asset_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class PlusModule(BaseModel):
    kind: Literal["view", "observation", "unknown"]
    heading: str | None = None
    paragraphs: list[str] = Field(default_factory=list)
    images: list[MaterialImage] = Field(default_factory=list)


class PlusInterpretation(BaseModel):
    plus_id: str | None = None
    title: str
    modules: list[PlusModule]
    fetched_at: dt.datetime


class MaterialIn(BaseModel):
    """插件推送的素材（只包含采集端负责的字段）"""

    id: uuid.UUID
    source: Literal["jin10"] = "jin10"
    source_item_id: str | None = Field(default=None, max_length=64)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    item_kind: ItemKind
    locked: bool = False
    tags: list[str] = Field(default_factory=list)
    detail_url: str | None = None
    title: str = Field(max_length=500)
    flash_title: str | None = None
    content: str
    content_raw: str
    plus_title: str | None = None
    has_plus_link: bool = False
    images: list[MaterialImage] = Field(default_factory=list)
    publish_time: dt.datetime | None = None
    url: str
    raw_html: str = Field(max_length=100_000)
    collect_time: dt.datetime
    collect_method: CollectMethod
    interpretation_status: InterpretationStatus
    interpretation: PlusInterpretation | None = None
    analyze_requested: bool = False
    deleted_at: dt.datetime | None = None


class MaterialOut(MaterialIn):
    model_config = ConfigDict(from_attributes=True)

    team_id: uuid.UUID
    category: str | None
    importance_score: int
    final_score: int
    level: str | None
    ai_status: str
    ai_reason: str | None
    ai_error: str | None
    ai_confidence: float | None
    analyzed_at: dt.datetime | None
    category_locked: bool
    edits: dict[str, Any] | None = None
    created_by: uuid.UUID | None
    collected_by: list[str]
    updated_at: dt.datetime
    schema_version: int


SyncOutcome = Literal["created", "merged", "unchanged", "deleted"]


class SyncPushRequest(BaseModel):
    materials: list[MaterialIn]


class SyncResult(BaseModel):
    #: 客户端提交时的 ID；服务端已有同一条素材时，material.id 为团队内既有 ID
    client_id: uuid.UUID
    outcome: SyncOutcome
    material: MaterialOut | None


class SyncPushResponse(BaseModel):
    results: list[SyncResult]


class ChangesResponse(BaseModel):
    items: list[MaterialOut]
    next_cursor: str | None
    has_more: bool


# ---------- 图片 ----------


class AssetExistsRequest(BaseModel):
    ids: list[str] = Field(max_length=200)


class AssetExistsResponse(BaseModel):
    missing: list[str]


# ---------- 选稿与生成 ----------

SectionId = Literal["macro", "cn_equity", "global_equity", "ai", "research", "charts"]


class MaterialPatch(BaseModel):
    """编辑在选稿页调整栏目（锁定后 AI 不再覆盖）"""

    category: SectionId


class IssueSection(BaseModel):
    id: SectionId
    material_ids: list[uuid.UUID] = Field(min_length=1, max_length=30)


class LayoutOverride(BaseModel):
    """One editable PPT element, positioned as a fraction of its slide."""

    page: int = Field(ge=1, le=100)
    shape_id: int = Field(ge=1)
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-f]{64}$')
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    font_size_pt: float | None = Field(default=None, ge=8, le=72)
    paragraphs: list[str] | None = Field(default=None, max_length=30)

    @model_validator(mode='after')
    def inside_page(self):
        if not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise ValueError('排版坐标必须为有限数字')
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError('排版元素必须留在页面内')
        if self.paragraphs is not None and (sum(map(len, self.paragraphs)) > 20_000 or
                                            any('\r' in text for text in self.paragraphs)):
            raise ValueError('编辑文字超出限制')
        return self


class GenerateRequest(BaseModel):
    issue_date: dt.date
    sections: list[IssueSection] = Field(min_length=1)
    #: 不传则系统推荐；显式列表（含空列表）为人工首页精选
    cover_material_ids: list[uuid.UUID] | None = Field(default=None, max_length=12)
    layout_overrides: list[LayoutOverride] = Field(default_factory=list, max_length=300)


class IssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    issue_date: dt.date
    sections: list[IssueSection]
    page_count: int
    warnings: list[str]
    has_pdf: bool
    created_by: uuid.UUID | None
    created_at: dt.datetime


class MaterialEdits(BaseModel):
    """选稿页的编辑内容；未提供的字段沿用采集原文"""

    title: str | None = Field(default=None, max_length=500)
    news: str | None = Field(default=None, max_length=20_000)
    interpretation: list[str] | None = Field(default=None, max_length=50)
    #: 进入 PPT 的配图（按顺序），为服务端已有图片的 ID；空列表表示不放图
    images: list[str] | None = Field(default=None, max_length=10)


class PreviewOut(BaseModel):
    page_count: int
    warnings: list[str]
    #: 每页 PNG（data URL），由最终 PDF 渲染得到，与生成结果一致
    pages: list[str]
    elements: list[list[dict[str, Any]]] = Field(default_factory=list)
