"""Structured data export for the team database.

Every dataset is registered here with explicit columns; new tables become exportable by
adding one entry. Sensitive material (password hashes, encrypted keys, raw capture HTML,
source snapshot binaries) is never registered, and only team-scoped rows are returned.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable, Iterable, Iterator

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.briefs.policy import CHINA
from app.db.models import (
    Asset, Board, BriefAudit, BriefIssue, BriefJob, BriefSourceSnapshot, BriefVersion,
    Feedback, Issue, Material, NewsAuditEvent, NewsDraft, NewsDraftRevision, NewsEvent,
    NewsEventAnalysis, NewsEventClaim, NewsEventClaimEvidence, NewsEventEvidence,
    NewsEventRevision, NewsSkillVersion, NewsWorkflowNode, NewsWorkflowRun,
    ParseReport, User,
)

MAX_ROWS = 50_000
UTC_OFFSET = timedelta(hours=8)  # Asia/Shanghai has no daylight saving time
EXCEL_CELL_LIMIT = 32_767


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str = 'text'  # text | int | bool | date | datetime | json | user
    value: Callable[[object], object] = field(default=lambda row: None)


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    description: str
    date_label: str
    day: bool                      # True when the filter column stores a plain date
    columns: tuple[Column, ...]
    count: Callable[[Session, uuid.UUID, date | None, date | None], int]
    rows: Callable[[Session, uuid.UUID, date | None, date | None], list[dict]]


def attr(name: str) -> Callable[[object], object]:
    return lambda row: getattr(row, name)


def dt_conditions(column, start: date | None, end: date | None) -> list:
    clauses = []
    if start:
        clauses.append(column >= datetime.combine(start, time.min) - UTC_OFFSET)
    if end:
        clauses.append(column < datetime.combine(end + timedelta(days=1), time.min) - UTC_OFFSET)
    return clauses


def day_conditions(column, start: date | None, end: date | None) -> list:
    clauses = []
    if start:
        clauses.append(column >= start)
    if end:
        clauses.append(column <= end)
    return clauses


def build_dataset(*, key: str, label: str, description: str, date_label: str, model,
                  team_column, date_column, columns: tuple[Column, ...],
                  join: tuple | None = None, day: bool = False, extra_select: tuple = (),
                  order_column=None) -> Dataset:
    """Register a single-table (or one-join) dataset filtered by its business date."""
    conditions = (day_conditions if day else dt_conditions)

    def filters(team_id: uuid.UUID, start: date | None, end: date | None) -> list:
        return [team_column == team_id, *conditions(date_column, start, end)]

    def count(db: Session, team_id: uuid.UUID, start: date | None, end: date | None) -> int:
        stmt = select(func.count()).select_from(model).where(*filters(team_id, start, end))
        if join:
            stmt = stmt.join(*join)
        return int(db.scalar(stmt) or 0)

    def rows(db: Session, team_id: uuid.UUID, start: date | None, end: date | None) -> list[dict]:
        stmt: Select = select(model, *extra_select).where(*filters(team_id, start, end))
        if join:
            stmt = stmt.join(*join)
        stmt = stmt.order_by(order_column if order_column is not None else date_column)
        out = []
        for row in db.execute(stmt):
            entity = row[0]
            values = {column.key: row[index + 1] for index, column in enumerate(extra_select)}
            for column in columns:
                if column.key not in values:
                    values[column.key] = column.value(entity)
            out.append({column.key: values.get(column.key) for column in columns})
        return out

    return Dataset(key=key, label=label, description=description, date_label=date_label,
                   day=day, columns=columns, count=count, rows=rows)


def _columns(*items: tuple) -> tuple[Column, ...]:
    out = []
    for item in items:
        key, label, kind = item
        out.append(Column(key, label, kind, attr(key)))
    return tuple(out)


MATERIALS = build_dataset(
    key='materials', label='素材库', date_label='采集时间',
    description='日报采集的素材正文、栏目与评分，包含已删除素材（见删除时间）；原始网页 HTML 不导出。',
    model=Material, team_column=Material.team_id, date_column=Material.collect_time,
    columns=_columns(
        ('id', '素材ID', 'text'), ('title', '标题', 'text'), ('flash_title', '快讯标题', 'text'),
        ('category', '栏目', 'text'), ('level', '等级', 'text'),
        ('importance_score', '重要度', 'int'), ('final_score', '最终评分', 'int'),
        ('ai_status', 'AI状态', 'text'), ('ai_reason', 'AI说明', 'text'),
        ('source', '来源', 'text'), ('source_item_id', '来源条目ID', 'text'),
        ('url', '原文链接', 'text'), ('detail_url', '详情页', 'text'),
        ('publish_time', '发布时间', 'datetime'), ('collect_time', '采集时间', 'datetime'),
        ('collect_method', '采集方式', 'text'), ('interpretation_status', '解读状态', 'text'),
        ('content', '正文', 'text'), ('interpretation', 'PLUS解读', 'json'),
        ('tags', '标签', 'json'), ('images', '图片', 'json'), ('edits', '编辑修改', 'json'),
        ('created_by', '创建人', 'user'),
        ('deleted_at', '删除时间', 'datetime'),
        ('created_at', '入库时间', 'datetime'), ('updated_at', '更新时间', 'datetime'),
    ),
)

ASSETS = build_dataset(
    key='assets', label='素材图片', date_label='入库时间',
    description='素材图片的登记信息与校验值；图片文件本体保存在对象存储，不包含在表格中。',
    model=Asset, team_column=Asset.team_id, date_column=Asset.created_at,
    columns=_columns(
        ('id', '图片ID', 'text'), ('source_url', '原始地址', 'text'), ('mime', '类型', 'text'),
        ('size', '字节数', 'int'), ('sha256', 'SHA-256', 'text'), ('path', '相对路径', 'text'),
        ('uploaded_by', '上传人', 'user'), ('created_at', '入库时间', 'datetime'),
    ),
)

ISSUES = build_dataset(
    key='issues', label='日报记录', date_label='日报日期',
    description='每日生成的日报版本、页数与导出文件路径。',
    model=Issue, team_column=Issue.team_id, date_column=Issue.issue_date, day=True,
    columns=_columns(
        ('id', '日报ID', 'text'), ('issue_date', '日报日期', 'date'),
        ('page_count', '页数', 'int'), ('warnings', '生成提示', 'json'),
        ('pptx_path', 'PPTX路径', 'text'), ('pdf_path', 'PDF路径', 'text'),
        ('created_by', '制作人', 'user'), ('created_at', '生成时间', 'datetime'),
    ),
)

BOARDS = build_dataset(
    key='boards', label='选稿板', date_label='选稿日期',
    description='团队选稿板的已选素材、首页精选与排序。',
    model=Board, team_column=Board.team_id, date_column=Board.day, day=True,
    columns=_columns(
        ('id', '选稿板ID', 'text'), ('day', '选稿日期', 'date'),
        ('picked', '已选素材', 'json'), ('cover_ids', '首页精选', 'json'),
        ('order', '栏目排序', 'json'), ('version', '版本', 'int'),
        ('updated_by', '最后修改人', 'user'), ('updated_at', '更新时间', 'datetime'),
    ),
)

PARSE_REPORTS = build_dataset(
    key='parse_reports', label='采集异常', date_label='上报时间',
    description='插件上报的采集与解析异常；出问题的原始 HTML 不导出。',
    model=ParseReport, team_column=ParseReport.team_id, date_column=ParseReport.created_at,
    columns=_columns(
        ('id', '记录ID', 'text'), ('kind', '类型', 'text'), ('source_item_id', '来源条目ID', 'text'),
        ('detail', '说明', 'text'), ('extension_version', '插件版本', 'text'),
        ('user_id', '上报人', 'user'), ('created_at', '上报时间', 'datetime'),
    ),
)

BRIEF_ISSUES = build_dataset(
    key='brief_issues', label='快讯期次', date_label='生产日期',
    description='固定快讯五个栏目的生产期次与确认状态。',
    model=BriefIssue, team_column=BriefIssue.team_id, date_column=BriefIssue.issue_date, day=True,
    columns=_columns(
        ('id', '期次ID', 'text'), ('issue_date', '生产日期', 'date'), ('category', '栏目', 'text'),
        ('version', '当前版本', 'int'), ('confirmed_version', '确认版本', 'int'),
        ('confirmed_by', '确认人', 'user'), ('confirmed_at', '确认时间', 'datetime'),
        ('updated_at', '更新时间', 'datetime'),
    ),
)

BRIEF_VERSIONS = build_dataset(
    key='brief_versions', label='快讯版本', date_label='生产日期',
    description='固定快讯全部稿件版本（含历史版本）；完整结构化数据见“稿件数据”列。',
    model=BriefVersion, team_column=BriefIssue.team_id, date_column=BriefIssue.issue_date, day=True,
    join=(BriefIssue, BriefVersion.issue_id == BriefIssue.id),
    extra_select=(BriefIssue.issue_date,),
    order_column=BriefIssue.issue_date,
    columns=_columns(
        ('id', '版本ID', 'text'), ('issue_id', '期次ID', 'text'), ('number', '版本号', 'int'),
        ('issue_date', '生产日期', 'date'),
        ('kind', '版本类型', 'text'), ('title', '标题', 'text'), ('dynamic_body', '动态正文', 'text'),
        ('payload', '稿件数据', 'json'), ('created_by', '作者', 'user'),
        ('created_at', '保存时间', 'datetime'),
    ),
)

BRIEF_JOBS = build_dataset(
    key='brief_jobs', label='快讯任务', date_label='生产日期',
    description='固定快讯任务队列：状态、尝试次数与错误信息。',
    model=BriefJob, team_column=BriefJob.team_id, date_column=BriefJob.issue_date, day=True,
    columns=_columns(
        ('id', '任务ID', 'text'), ('issue_date', '生产日期', 'date'), ('category', '栏目', 'text'),
        ('action', '动作', 'text'), ('preview', '测试运行', 'bool'), ('status', '状态', 'text'),
        ('attempts', '尝试次数', 'int'), ('error', '错误', 'text'),
        ('created_by', '发起人', 'user'), ('next_attempt_at', '下次尝试', 'datetime'),
        ('created_at', '创建时间', 'datetime'), ('updated_at', '更新时间', 'datetime'),
    ),
)

BRIEF_SNAPSHOTS = build_dataset(
    key='brief_source_snapshots', label='快讯来源快照', date_label='数据日',
    description='固定快讯每次运行归档的来源文件清单与校验值；原始文件内容保存在数据卷，不包含在表格中。',
    model=BriefSourceSnapshot, team_column=BriefSourceSnapshot.team_id,
    date_column=BriefSourceSnapshot.data_date, day=True,
    columns=_columns(
        ('id', '快照ID', 'text'), ('job_id', '任务ID', 'text'), ('version_id', '版本ID', 'text'),
        ('attempt', '尝试次数', 'int'), ('category', '栏目', 'text'),
        ('data_date', '数据日', 'date'), ('relative_path', '相对路径', 'text'),
        ('sha256', 'SHA-256', 'text'), ('byte_count', '字节数', 'int'),
        ('captured_at', '归档时间', 'datetime'),
    ),
)

BRIEF_AUDITS = build_dataset(
    key='brief_audits', label='快讯审核记录', date_label='操作时间',
    description='固定快讯的确认、撤回与保存等操作记录。',
    model=BriefAudit, team_column=BriefIssue.team_id, date_column=BriefAudit.created_at,
    join=(BriefIssue, BriefAudit.issue_id == BriefIssue.id),
    order_column=BriefAudit.created_at,
    columns=_columns(
        ('id', '记录ID', 'text'), ('issue_id', '期次ID', 'text'), ('action', '操作', 'text'),
        ('version', '版本号', 'int'), ('user_id', '操作人', 'user'),
        ('created_at', '操作时间', 'datetime'),
    ),
)

FEEDBACK = build_dataset(
    key='feedback', label='使用者反馈', date_label='提交时间',
    description='使用者提交的改进意见、提交人姓名与提交账号；示意图文件保存在数据卷，这里给出类型、大小与校验值。',
    model=Feedback, team_column=Feedback.team_id, date_column=Feedback.created_at,
    columns=_columns(
        ('id', '反馈ID', 'text'), ('title', '标题', 'text'), ('content', '内容', 'text'),
        ('name', '提交人姓名', 'text'), ('user_id', '提交账号', 'user'),
        ('image_mime', '示意图类型', 'text'), ('image_bytes', '示意图字节数', 'int'),
        ('image_sha256', '示意图SHA-256', 'text'), ('created_at', '提交时间', 'datetime'),
    ) + (Column('has_image', '有示意图', 'bool', lambda row: bool(row.image_path)),),
)

NEWS_EVENTS = build_dataset(
    key='news_events', label='新闻事件', date_label='事件时间',
    description='News Skill OS 团队新闻事件的稳定身份；正文事实见“新闻事件修订”。',
    model=NewsEvent, team_column=NewsEvent.team_id, date_column=NewsEvent.event_time,
    columns=_columns(
        ('id', '事件ID', 'text'), ('event_key', '事件标识', 'text'), ('title', '标题', 'text'),
        ('event_time', '事件时间', 'datetime'), ('status', '状态', 'text'),
        ('current_revision', '当前修订', 'int'), ('payload', '事件元数据', 'json'),
        ('created_by', '创建人', 'user'), ('created_at', '创建时间', 'datetime'),
        ('updated_at', '更新时间', 'datetime'),
    ),
)

NEWS_EVENT_REVISIONS = build_dataset(
    key='news_event_revisions', label='新闻事件修订', date_label='创建时间',
    description='不可变的事件事实快照（来源、证据、Claim、分析与草稿）；用于重建当时采用的事实。',
    model=NewsEventRevision, team_column=NewsEventRevision.team_id,
    date_column=NewsEventRevision.created_at,
    columns=_columns(
        ('id', '修订ID', 'text'), ('event_id', '事件ID', 'text'), ('number', '修订号', 'int'),
        ('snapshot_sha256', '快照SHA-256', 'text'), ('audit_reason', '修订原因', 'text'),
        ('snapshot', '事实快照', 'json'), ('created_by', '修订人', 'user'),
        ('created_at', '创建时间', 'datetime'),
    ),
)

NEWS_EVENT_EVIDENCE = build_dataset(
    key='news_event_evidence', label='新闻事件证据', date_label='证据时间',
    description='修订级证据与来源归属；转载共享原始来源标识，不导出原始网页 HTML。',
    model=NewsEventEvidence, team_column=NewsEventEvidence.team_id,
    date_column=NewsEventEvidence.captured_at,
    columns=_columns(
        ('id', '证据ID', 'text'), ('event_id', '事件ID', 'text'), ('revision_id', '修订ID', 'text'),
        ('evidence_key', '证据键', 'text'), ('source_key', '来源键', 'text'),
        ('source_name', '来源名称', 'text'), ('source_kind', '来源类型', 'text'),
        ('source_original_id', '原始来源ID', 'text'), ('source_published_at', '来源发布时间', 'datetime'),
        ('excerpt', '证据摘录', 'text'), ('captured_at', '证据时间', 'datetime'),
        ('locator', '定位', 'text'), ('source_snapshot_hash', '来源快照SHA-256', 'text'),
        ('payload', '证据数据', 'json'), ('created_at', '入库时间', 'datetime'),
    ),
)

NEWS_EVENT_CLAIMS = build_dataset(
    key='news_event_claims', label='新闻事件 Claim', date_label='创建时间',
    description='修订级原子断言；数值口径、币种、期间与核验状态以结构化数据保存。',
    model=NewsEventClaim, team_column=NewsEventClaim.team_id, date_column=NewsEventClaim.created_at,
    columns=_columns(
        ('id', 'ClaimID', 'text'), ('event_id', '事件ID', 'text'), ('revision_id', '修订ID', 'text'),
        ('claim_key', 'Claim键', 'text'), ('subject', '主体', 'text'), ('predicate', '谓语', 'text'),
        ('text', '断言', 'text'), ('status', '核验状态', 'text'), ('numeric', '数值口径', 'json'),
        ('attributed_to', '归属', 'text'), ('superseded_by', '替代Claim', 'text'),
        ('payload', 'Claim数据', 'json'), ('created_at', '创建时间', 'datetime'),
    ),
)

NEWS_EVENT_LINKS = build_dataset(
    key='news_event_claim_evidence', label='新闻事件证据关系', date_label='创建时间',
    description='Claim 与 Evidence 的支持/矛盾关系，用于追溯事实链。',
    model=NewsEventClaimEvidence, team_column=NewsEventClaimEvidence.team_id,
    date_column=NewsEventClaimEvidence.created_at,
    columns=_columns(
        ('id', '关系ID', 'text'), ('event_id', '事件ID', 'text'), ('revision_id', '修订ID', 'text'),
        ('claim_id', 'ClaimID', 'text'), ('evidence_id', '证据ID', 'text'),
        ('stance', '立场', 'text'), ('created_at', '创建时间', 'datetime'),
    ),
)

NEWS_EVENT_ANALYSES = build_dataset(
    key='news_event_analyses', label='新闻事件分析', date_label='创建时间',
    description='前因/后果/补充说明三段分析及事实/推断标签，必须携带 Claim 或 Evidence 依赖。',
    model=NewsEventAnalysis, team_column=NewsEventAnalysis.team_id,
    date_column=NewsEventAnalysis.created_at,
    columns=_columns(
        ('id', '分析ID', 'text'), ('event_id', '事件ID', 'text'), ('revision_id', '修订ID', 'text'),
        ('analysis_key', '分析键', 'text'), ('section', '段落', 'text'), ('text', '正文', 'text'),
        ('label', '事实或推断', 'text'), ('claim_ids', 'Claim依赖', 'json'),
        ('evidence_ids', '证据依赖', 'json'), ('payload', '分析数据', 'json'),
        ('created_at', '创建时间', 'datetime'),
    ),
)

NEWS_DRAFTS = build_dataset(
    key='news_drafts', label='新闻草稿', date_label='更新时间',
    description='Plus新鲜事 / Plus注解等待审草稿的当前版本；通过审核不等于对外发布。',
    model=NewsDraft, team_column=NewsDraft.team_id, date_column=NewsDraft.updated_at,
    columns=_columns(
        ('id', '草稿ID', 'text'), ('event_id', '事件ID', 'text'), ('title', '标题', 'text'),
        ('body', '正文', 'text'), ('target', '目标栏目', 'text'), ('status', '状态', 'text'),
        ('current_revision', '当前修订', 'int'), ('skill_id', 'SkillID', 'text'),
        ('skill_version', 'Skill版本', 'text'), ('payload', '草稿数据', 'json'),
        ('created_by', '创建人', 'user'), ('created_at', '创建时间', 'datetime'),
        ('updated_at', '更新时间', 'datetime'),
    ),
)

NEWS_DRAFT_REVISIONS = build_dataset(
    key='news_draft_revisions', label='新闻草稿修订', date_label='创建时间',
    description='不可变草稿版本与人工审核记录；审核只影响状态，不改正文历史。',
    model=NewsDraftRevision, team_column=NewsDraftRevision.team_id,
    date_column=NewsDraftRevision.created_at,
    columns=_columns(
        ('id', '修订ID', 'text'), ('draft_id', '草稿ID', 'text'), ('number', '修订号', 'int'),
        ('event_revision_id', '事件修订ID', 'text'), ('title', '标题', 'text'),
        ('body', '正文', 'text'), ('skill_id', 'SkillID', 'text'),
        ('skill_version', 'Skill版本', 'text'), ('payload', '草稿数据', 'json'),
        ('review_status', '审核状态', 'text'), ('review_note', '审核意见', 'text'),
        ('reviewed_by', '审核人', 'user'), ('reviewed_at', '审核时间', 'datetime'),
        ('created_by', '创建人', 'user'), ('created_at', '创建时间', 'datetime'),
    ),
)

NEWS_SKILL_VERSIONS = build_dataset(
    key='news_skill_versions', label='新闻 Skill 版本', date_label='登记时间',
    description='团队使用过的 Skill Manifest 不可变快照、摘要与权限声明。',
    model=NewsSkillVersion, team_column=NewsSkillVersion.team_id,
    date_column=NewsSkillVersion.created_at,
    columns=_columns(
        ('id', '记录ID', 'text'), ('skill_id', 'SkillID', 'text'), ('version', '版本', 'text'),
        ('digest', 'Manifest摘要', 'text'), ('manifest', 'Manifest', 'json'),
        ('source', '来源', 'text'), ('created_by', '登记人', 'user'),
        ('created_at', '登记时间', 'datetime'),
    ),
)

NEWS_WORKFLOW_RUNS = build_dataset(
    key='news_workflow_runs', label='新闻工作流运行', date_label='创建时间',
    description='DAG 运行状态、输入/输出摘要与错误；没有发布动作。',
    model=NewsWorkflowRun, team_column=NewsWorkflowRun.team_id,
    date_column=NewsWorkflowRun.created_at,
    columns=_columns(
        ('id', '运行ID', 'text'), ('run_key', '运行键', 'text'), ('event_id', '事件ID', 'text'),
        ('event_revision', '事件修订', 'int'),
        ('workflow_id', '工作流ID', 'text'), ('workflow_digest', '工作流摘要', 'text'),
        ('status', '状态', 'text'), ('input_digest', '输入摘要', 'text'),
        ('output', '输出', 'json'), ('error', '错误', 'text'),
        ('started_at', '开始时间', 'datetime'), ('finished_at', '结束时间', 'datetime'),
        ('created_by', '发起人', 'user'), ('created_at', '创建时间', 'datetime'),
    ),
)

NEWS_WORKFLOW_NODES = build_dataset(
    key='news_workflow_nodes', label='新闻工作流节点', date_label='创建时间',
    description='节点级 Skill 版本、输入/输出摘要、来源快照、缓存、耗时与重试。',
    model=NewsWorkflowNode, team_column=NewsWorkflowNode.team_id,
    date_column=NewsWorkflowNode.created_at,
    columns=_columns(
        ('id', '节点记录ID', 'text'), ('run_id', '运行ID', 'text'), ('node_id', '节点ID', 'text'),
        ('skill_id', 'SkillID', 'text'), ('skill_version', 'Skill版本', 'text'),
        ('status', '状态', 'text'), ('input_digest', '输入摘要', 'text'),
        ('output_digest', '输出摘要', 'text'), ('source_snapshot_hashes', '来源快照', 'json'),
        ('duration_ms', '耗时毫秒', 'int'), ('attempts', '尝试次数', 'int'),
        ('cached', '缓存命中', 'bool'), ('error', '错误', 'text'), ('output', '输出', 'json'),
        ('created_at', '创建时间', 'datetime'),
    ),
)

NEWS_AUDIT = build_dataset(
    key='news_audit_events', label='新闻操作审计', date_label='操作时间',
    description='News Skill OS 的追加式人工写操作审计；不包含密钥或原始网页 HTML。',
    model=NewsAuditEvent, team_column=NewsAuditEvent.team_id,
    date_column=NewsAuditEvent.created_at,
    columns=_columns(
        ('id', '审计ID', 'text'), ('entity_type', '对象类型', 'text'),
        ('entity_id', '对象ID', 'text'), ('action', '操作', 'text'), ('revision', '修订号', 'int'),
        ('actor_name', '操作人', 'text'), ('reason', '原因', 'text'),
        ('details', '详情', 'json'), ('created_at', '操作时间', 'datetime'),
    ),
)

DATASETS: dict[str, Dataset] = {dataset.key: dataset for dataset in (
    MATERIALS, ASSETS, ISSUES, BOARDS, PARSE_REPORTS,
    BRIEF_ISSUES, BRIEF_VERSIONS, BRIEF_JOBS, BRIEF_SNAPSHOTS, BRIEF_AUDITS, FEEDBACK,
    NEWS_EVENTS, NEWS_EVENT_REVISIONS, NEWS_EVENT_EVIDENCE, NEWS_EVENT_CLAIMS,
    NEWS_EVENT_LINKS, NEWS_EVENT_ANALYSES, NEWS_DRAFTS, NEWS_DRAFT_REVISIONS,
    NEWS_SKILL_VERSIONS, NEWS_WORKFLOW_RUNS, NEWS_WORKFLOW_NODES, NEWS_AUDIT,
)}


def headers(dataset: Dataset) -> list[str]:
    return [column.label for column in dataset.columns]


def format_datetime(value: datetime) -> str:
    stamp = value.astimezone(CHINA) if value.tzinfo else value + UTC_OFFSET
    return stamp.strftime('%Y-%m-%d %H:%M:%S')


def user_map(db: Session, team_id: uuid.UUID) -> dict[str, str]:
    return {str(row.id): row.name for row in db.scalars(select(User).where(User.team_id == team_id))}


def text_cell(column: Column, value, users: dict[str, str]) -> str | int | None:
    if value is None:
        return None
    if column.kind == 'datetime':
        return format_datetime(value)
    if column.kind == 'date':
        return value.isoformat()
    if column.kind == 'json':
        return json.dumps(value, ensure_ascii=False, default=str)
    if column.kind == 'bool':
        return 'true' if value else 'false'
    if column.kind == 'user':
        return users.get(str(value), str(value))
    if column.kind == 'int':
        return value
    return str(value)


def json_cell(column: Column, value, users: dict[str, str]):
    if value is None:
        return None
    if column.kind == 'datetime':
        return format_datetime(value)
    if column.kind == 'date':
        return value.isoformat()
    if column.kind == 'user':
        return users.get(str(value), str(value))
    if column.kind == 'json':
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def csv_bytes(dataset: Dataset, rows: Iterable[dict], users: dict[str, str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    writer.writerow(headers(dataset))
    for row in rows:
        writer.writerow([text_cell(column, row.get(column.key), users) for column in dataset.columns])
    # The BOM keeps Chinese readable when Excel opens the file directly.
    return ('\ufeff' + buffer.getvalue()).encode('utf-8')


def json_bytes(dataset: Dataset, rows: Iterable[dict], users: dict[str, str]) -> bytes:
    payload = [{column.key: json_cell(column, row.get(column.key), users) for column in dataset.columns}
               for row in rows]
    return json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')


def xlsx_bytes(dataset: Dataset, rows: Iterable[dict], users: dict[str, str]) -> bytes:
    from openpyxl import Workbook

    book = Workbook(write_only=True)
    sheet = book.create_sheet(title='数据')
    sheet.append(headers(dataset))
    for row in rows:
        values = []
        for column in dataset.columns:
            value = text_cell(column, row.get(column.key), users)
            if isinstance(value, str) and len(value) > EXCEL_CELL_LIMIT:
                value = value[:EXCEL_CELL_LIMIT - 1] + '…'
            values.append(value)
        sheet.append(values)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
