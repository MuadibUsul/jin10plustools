"""数据表。素材字段与插件 src/types/material.ts 的 NewsMaterial 一一对应。"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: PostgreSQL 用 JSONB，测试用的 SQLite 退回 JSON
JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dt.datetime: DateTime(timezone=True)}


class Role(StrEnum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    #: 栏目配额、权重系数等团队设置（P5/P6 使用）
    settings: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    users: Mapped[list[User]] = relationship(back_populates="team")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.EDITOR)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    team: Mapped[Team] = relationship(back_populates="users")


class Material(Base):
    """团队素材。同一团队内按金十条目 ID 或内容指纹唯一。"""

    __tablename__ = "materials"
    __table_args__ = (
        Index(
            "uq_materials_team_item",
            "team_id",
            "source_item_id",
            unique=True,
            postgresql_where=text("source_item_id IS NOT NULL"),
            sqlite_where=text("source_item_id IS NOT NULL"),
        ),
        Index("uq_materials_team_fingerprint", "team_id", "fingerprint", unique=True),
        Index("ix_materials_team_sync", "team_id", "updated_at", "id"),
        Index("ix_materials_team_collect", "team_id", "collect_time"),
        Index("ix_materials_ai_queue", "ai_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))

    # —— 采集内容 ——
    source: Mapped[str] = mapped_column(String(20), default="jin10")
    source_item_id: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    item_kind: Mapped[str] = mapped_column(String(20))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list)
    detail_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    flash_title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    content_raw: Mapped[str] = mapped_column(Text)
    plus_title: Mapped[str | None] = mapped_column(Text)
    has_plus_link: Mapped[bool] = mapped_column(Boolean, default=False)
    images: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    publish_time: Mapped[dt.datetime | None]
    url: Mapped[str] = mapped_column(Text)
    raw_html: Mapped[str] = mapped_column(Text)
    collect_time: Mapped[dt.datetime]
    collect_method: Mapped[str] = mapped_column(String(20))

    # —— PLUS 解读 ——
    interpretation_status: Mapped[str] = mapped_column(String(20))
    interpretation: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    analyze_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    # —— AI 与编辑（服务端维护，客户端推送不会覆盖）——
    category: Mapped[str | None] = mapped_column(String(30))
    importance_score: Mapped[int] = mapped_column(Integer, default=0)
    final_score: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[str | None] = mapped_column(String(1))
    ai_status: Mapped[str] = mapped_column(String(20), default="pending")
    ai_reason: Mapped[str | None] = mapped_column(Text)
    ai_error: Mapped[str | None] = mapped_column(Text)
    #: 栏目分类置信度（0–1），低于阈值时编排台提示人工确认
    ai_confidence: Mapped[float | None] = mapped_column(Float)
    #: JEV 原始答案（各题得分、概率分布），便于排查与调参
    ai_details: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    ai_attempts: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_at: Mapped[dt.datetime | None]
    #: 编辑手动指定了栏目：之后的 AI 重新分析不再覆盖
    category_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    #: 编辑在选稿页的修改（不覆盖采集原文）：{"title", "news", "interpretation": [段落], "images": [asset_id]}
    #: 缺省的键沿用原文；整体为空表示未编辑
    edits: Mapped[dict[str, Any] | None] = mapped_column(JsonType)

    # —— 协作 ——
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    #: 收藏过该素材的成员 ID（团队热度）
    collected_by: Mapped[list[str]] = mapped_column(JsonType, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    #: 服务端写入时间，作为增量同步游标（客户端时间不可信）
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[dt.datetime | None]
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


class Asset(Base):
    """素材图片。ID = sha256(原图地址)[:32]，与插件一致，同一张图团队内只存一份。"""

    __tablename__ = "assets"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(50))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    #: 相对 storage_dir 的路径（仅供排查；读取时按约定路径定位，数据卷可整体迁移）
    path: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Issue(Base):
    """一期生成的日报（选中的素材、页数、生成文件）"""

    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    #: [{"id": 栏目, "material_ids": [...]}]，顺序即日报中的顺序
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JsonType, default=list)
    #: 相对 storage_dir 的文件路径；PDF 转换失败时为空
    pptx_path: Mapped[str] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Board(Base):
    """团队某一天的共享选稿板：已选素材与各栏目的手动排序（多账号协作）"""

    __tablename__ = "boards"
    __table_args__ = (Index("uq_boards_team_day", "team_id", "day", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    day: Mapped[dt.date] = mapped_column(Date)
    #: 已选入日报的素材 ID
    picked: Mapped[list[str]] = mapped_column(JsonType, default=list)
    #: {栏目: [素材 ID, ...]}，拖拽排过序的素材顺序
    order: Mapped[dict[str, list[str]]] = mapped_column(JsonType, default=dict)
    #: 每次修改 +1，前端据此判断是否有他人改动
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ParseReport(Base):
    """插件上报的采集 / 解析异常，集中出现时说明金十页面改版"""

    __tablename__ = "parse_reports"
    __table_args__ = (Index("ix_parse_reports_team_created", "team_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    #: parse_failed / unrecognized_title / image_failed / plus_failed
    kind: Mapped[str] = mapped_column(String(40))
    source_item_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    #: 出问题的快讯 HTML（截断），便于对照金十新结构修解析器
    html: Mapped[str] = mapped_column(Text, default="")
    extension_version: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class BriefConfig(Base):
    __tablename__ = 'brief_configs'
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class BriefIssue(Base):
    __tablename__ = 'brief_issues'
    __table_args__ = (Index('uq_brief_issue', 'team_id', 'issue_date', 'category', unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_version: Mapped[int | None] = mapped_column(Integer)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    confirmed_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class BriefVersion(Base):
    __tablename__ = 'brief_versions'
    __table_args__ = (Index('uq_brief_version', 'issue_id', 'number', unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('brief_issues.id', ondelete='CASCADE'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), default='generated')
    title: Mapped[str] = mapped_column(Text)
    dynamic_body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class BriefJob(Base):
    __tablename__ = 'brief_jobs'
    __table_args__ = (Index('uq_brief_job_key', 'dedupe_key', unique=True), Index('ix_brief_job_queue', 'status', 'next_attempt_at'))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    issue_date: Mapped[dt.date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(20), default='produce')
    preview: Mapped[bool] = mapped_column(Boolean, default=False)
    dedupe_key: Mapped[str] = mapped_column(String(180))
    active_key: Mapped[str | None] = mapped_column(String(180), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    lease_until: Mapped[dt.datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class BriefAudit(Base):
    __tablename__ = 'brief_audits'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('brief_issues.id', ondelete='CASCADE'), index=True)
    action: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
