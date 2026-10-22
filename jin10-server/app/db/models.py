"""数据表。素材字段与插件 src/types/material.ts 的 NewsMaterial 一一对应。"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer,
    LargeBinary, Numeric, String, Text, text,
)
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
    #: None 为系统推荐；列表为人工精选（空列表表示不展示首页标题）
    cover_ids: Mapped[list[str] | None] = mapped_column(JsonType, nullable=True)
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
    notes: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
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
    next_attempt_at: Mapped[dt.datetime] = mapped_column(default=lambda: utcnow())
    lease_until: Mapped[dt.datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class BriefSourceSnapshot(Base):
    """Immutable-by-attempt copy of every fetched or normalized brief data file."""
    __tablename__ = 'brief_source_snapshots'
    __table_args__ = (Index('uq_brief_source_file', 'job_id', 'attempt', 'relative_path', unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('brief_jobs.id', ondelete='CASCADE'), index=True)
    version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('brief_versions.id', ondelete='SET NULL'))
    attempt: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(30))
    data_date: Mapped[dt.date] = mapped_column(Date)
    relative_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    captured_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ValuationObservation(Base):
    """One team-scoped, validated ERP input/output row per trading date."""
    __tablename__ = 'valuation_observations'
    __table_args__ = (Index('uq_valuation_observation_team_date', 'team_id', 'date', unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'))
    date: Mapped[dt.date] = mapped_column(Date)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    pe_ttm_mcw: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    bond_yield_number: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    erp: Mapped[Decimal] = mapped_column(Numeric(20, 12))
    source_status: Mapped[str | None] = mapped_column(String(100))
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('brief_jobs.id', ondelete='SET NULL'))


class BriefAudit(Base):
    __tablename__ = 'brief_audits'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    issue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('brief_issues.id', ondelete='CASCADE'), index=True)
    action: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Feedback(Base):
    """使用者提交的改进意见；图片保存在数据卷，表内只登记元数据与校验值。"""
    __tablename__ = 'feedback'
    __table_args__ = (Index('ix_feedback_team_created', 'team_id', 'created_at'),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    #: 提交人填写的姓名（与账号名分开保存，便于后续对接）
    name: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(String(1024))
    image_mime: Mapped[str | None] = mapped_column(String(50))
    image_bytes: Mapped[int] = mapped_column(Integer, default=0)
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: utcnow())


# ---------- News Skill OS：独立、团队隔离、默认关闭的内容事实链 ----------
# 这些表只由 /api/news-skill-os 在开关打开时写入；不参与日报、固定快讯和插件路径。
# 事件修订与草稿修订均不可变；审核、审计和运行记录只追加。

class NewsEvent(Base):
    """团队新闻事件的稳定身份；具体事实由不可变的 NewsEventRevision 保存。"""
    __tablename__ = 'news_events'
    __table_args__ = (
        Index('uq_news_events_team_key', 'team_id', 'event_key', unique=True,
              postgresql_where=text('event_key IS NOT NULL'), sqlite_where=text('event_key IS NOT NULL')),
        Index('ix_news_events_team_time', 'team_id', 'event_time'),
        CheckConstraint("status IN ('open','closed','archived')", name='ck_news_events_status'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_key: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(Text)
    event_time: Mapped[dt.datetime]
    status: Mapped[str] = mapped_column(String(20), default='open')
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsEventRevision(Base):
    """ArtifactRevision 的完整快照；旧文章可按 number 重建当时采用的事实。"""
    __tablename__ = 'news_event_revisions'
    __table_args__ = (
        Index('uq_news_event_revision', 'event_id', 'number', unique=True),
        Index('ix_news_event_revisions_team_time', 'team_id', 'created_at'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_events.id', ondelete='CASCADE'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    audit_reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsEventEvidence(Base):
    """修订级证据行；同一原始来源的转载共享 original_id，来源字段随证据保存。"""
    __tablename__ = 'news_event_evidence'
    __table_args__ = (
        Index('uq_news_evidence_revision_key', 'revision_id', 'evidence_key', unique=True),
        Index('ix_news_evidence_team_event', 'team_id', 'event_id', 'captured_at'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_events.id', ondelete='CASCADE'), index=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_event_revisions.id', ondelete='CASCADE'), index=True)
    evidence_key: Mapped[str] = mapped_column(String(200))
    source_key: Mapped[str] = mapped_column(String(200))
    source_name: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_original_id: Mapped[str] = mapped_column(String(300))
    source_published_at: Mapped[dt.datetime | None]
    excerpt: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[dt.datetime]
    locator: Mapped[str | None] = mapped_column(String(1024))
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsEventClaim(Base):
    """修订级原子断言；数值口径以结构化 payload 保存，状态变更走新修订。"""
    __tablename__ = 'news_event_claims'
    __table_args__ = (
        Index('uq_news_claim_revision_key', 'revision_id', 'claim_key', unique=True),
        Index('ix_news_claims_team_event', 'team_id', 'event_id', 'claim_key'),
        CheckConstraint("status IN ('unverified','corroborated','verified','disputed','rejected','superseded')",
                        name='ck_news_claim_status'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_events.id', ondelete='CASCADE'), index=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_event_revisions.id', ondelete='CASCADE'), index=True)
    claim_key: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='unverified')
    numeric: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    attributed_to: Mapped[str | None] = mapped_column(Text)
    superseded_by: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsEventClaimEvidence(Base):
    __tablename__ = 'news_event_claim_evidence'
    __table_args__ = (
        Index('uq_news_claim_evidence_pair', 'claim_id', 'evidence_id', unique=True),
        Index('ix_news_claim_evidence_team_event', 'team_id', 'event_id'),
        CheckConstraint("stance IN ('supports','contradicts')", name='ck_news_link_stance'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_events.id', ondelete='CASCADE'), index=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_event_revisions.id', ondelete='CASCADE'), index=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_event_claims.id', ondelete='CASCADE'))
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_event_evidence.id', ondelete='CASCADE'))
    stance: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsEventAnalysis(Base):
    __tablename__ = 'news_event_analyses'
    __table_args__ = (
        Index('uq_news_analysis_revision_key', 'revision_id', 'analysis_key', unique=True),
        Index('ix_news_analyses_team_event', 'team_id', 'event_id', 'section'),
        CheckConstraint("section IN ('前因','后果','补充说明')", name='ck_news_analysis_section'),
        CheckConstraint("label IN ('fact','inference')", name='ck_news_analysis_label'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_events.id', ondelete='CASCADE'), index=True)
    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_event_revisions.id', ondelete='CASCADE'), index=True)
    analysis_key: Mapped[str] = mapped_column(String(200))
    section: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(20))
    claim_ids: Mapped[list[str]] = mapped_column(JsonType, default=list)
    evidence_ids: Mapped[list[str]] = mapped_column(JsonType, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsDraft(Base):
    """Plus新鲜事 / Plus注解等待审草稿的当前头；正文历史保存在 NewsDraftRevision。"""
    __tablename__ = 'news_drafts'
    __table_args__ = (
        Index('ix_news_drafts_team_updated', 'team_id', 'updated_at'),
        Index('ix_news_drafts_team_event', 'team_id', 'event_id'),
        CheckConstraint("target IN ('fresh','annotation','brief_text','research')", name='ck_news_draft_target'),
        CheckConstraint("status IN ('draft','pending_review','approved','rejected','archived')",
                        name='ck_news_draft_status'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('news_events.id', ondelete='SET NULL'), index=True)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(String(20), default='fresh')
    status: Mapped[str] = mapped_column(String(20), default='draft')
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    skill_id: Mapped[str | None] = mapped_column(String(200))
    skill_version: Mapped[str | None] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsDraftRevision(Base):
    """不可变草稿版本；审核只写入本版本的审核元数据，不改正文。"""
    __tablename__ = 'news_draft_revisions'
    __table_args__ = (
        Index('uq_news_draft_revision', 'draft_id', 'number', unique=True),
        Index('ix_news_draft_revisions_team_time', 'team_id', 'created_at'),
        CheckConstraint("review_status IN ('pending','approved','rejected')", name='ck_news_draft_review_status'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_drafts.id', ondelete='CASCADE'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    event_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey('news_event_revisions.id', ondelete='SET NULL'))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    skill_id: Mapped[str | None] = mapped_column(String(200))
    skill_version: Mapped[str | None] = mapped_column(String(30))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    review_status: Mapped[str] = mapped_column(String(20), default='pending')
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    reviewed_at: Mapped[dt.datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsSkillVersion(Base):
    """团队首次使用某个 Skill 时的不可变 Manifest 快照与摘要。"""
    __tablename__ = 'news_skill_versions'
    __table_args__ = (
        Index('uq_news_skill_version', 'team_id', 'skill_id', 'version', unique=True),
        Index('ix_news_skill_versions_team_time', 'team_id', 'created_at'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    skill_id: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(30))
    digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, Any]] = mapped_column(JsonType)
    source: Mapped[str] = mapped_column(String(20), default='bundled')
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsWorkflowRun(Base):
    """一次 DAG 运行的持久化状态；不包含发布动作。"""
    __tablename__ = 'news_workflow_runs'
    __table_args__ = (
        Index('uq_news_workflow_run_key', 'run_key', unique=True),
        Index('ix_news_workflow_runs_team_time', 'team_id', 'created_at'),
        Index('ix_news_workflow_runs_team_status', 'team_id', 'status'),
        CheckConstraint("status IN ('queued','running','succeeded','failed','blocked','cancelled')",
                        name='ck_news_workflow_run_status'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_key: Mapped[str] = mapped_column(String(64))
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('news_events.id', ondelete='SET NULL'))
    event_revision: Mapped[int | None] = mapped_column(Integer)
    workflow_id: Mapped[str] = mapped_column(String(200))
    workflow_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    input: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    input_digest: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime | None]
    finished_at: Mapped[dt.datetime | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsWorkflowNode(Base):
    """节点级运行记录：摘要、缓存、耗时、尝试次数和错误，便于局部重跑。"""
    __tablename__ = 'news_workflow_nodes'
    __table_args__ = (
        Index('uq_news_workflow_node', 'run_id', 'node_id', unique=True),
        Index('ix_news_workflow_nodes_team_run', 'team_id', 'run_id'),
        CheckConstraint("status IN ('queued','running','succeeded','failed','blocked','cancelled')",
                        name='ck_news_workflow_node_status'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('news_workflow_runs.id', ondelete='CASCADE'), index=True)
    node_id: Mapped[str] = mapped_column(String(200))
    skill_id: Mapped[str] = mapped_column(String(200))
    skill_version: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    input_digest: Mapped[str] = mapped_column(String(64))
    output_digest: Mapped[str | None] = mapped_column(String(64))
    source_snapshot_hashes: Mapped[list[str]] = mapped_column(JsonType, default=list)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class NewsAuditEvent(Base):
    """每条人工写操作都追加审计；不提供更新或删除接口。"""
    __tablename__ = 'news_audit_events'
    __table_args__ = (
        Index('ix_news_audit_team_time', 'team_id', 'created_at'),
        Index('ix_news_audit_entity', 'team_id', 'entity_type', 'entity_id'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'), index=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int | None] = mapped_column(Integer)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    actor_name: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text, default='')
    details: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

class AIComputeObservation(Base):
    """Team-scoped Token and GPU rental observations imported from source workbooks."""
    __tablename__ = 'ai_compute_observations'
    __table_args__ = (
        Index('uq_ai_compute_team_kind_date', 'team_id', 'kind', 'date', unique=True),
        Index('ix_ai_compute_team_kind_date', 'team_id', 'kind', 'date'),
        CheckConstraint("kind IN ('token', 'gpu')", name='ck_ai_compute_kind'),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teams.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(20))
    date: Mapped[dt.date] = mapped_column(Date)
    token_price: Mapped[float | None] = mapped_column(Numeric(20, 6))
    a100: Mapped[float | None] = mapped_column(Numeric(20, 6))
    h100: Mapped[float | None] = mapped_column(Numeric(20, 6))
    b200: Mapped[float | None] = mapped_column(Numeric(20, 6))
    source: Mapped[str] = mapped_column(String(200), default='Silicon Data')
    source_status: Mapped[str | None] = mapped_column(String(100))
    public_display: Mapped[dict | None] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
