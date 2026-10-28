"""Team-scoped persistence for news facts and drafts.

This module is deliberately separate from the fixed-brief tables and workers.
Every public function requires a team_id from the authenticated user; callers
must never pass an event or draft ID without it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any, Iterable, Mapping

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    NewsAuditEvent, NewsDraft, NewsDraftRevision, NewsEvent, NewsEventAnalysis,
    NewsEventClaim, NewsEventClaimEvidence, NewsEventEvidence, NewsEventRevision,
    NewsSkillVersion,
)
from app.news_skill_os.models import (
    Analysis, ArtifactRevision, AuditEvent, Claim, ClaimEvidenceLink, Draft, Evidence,
    NewsEvent as DomainEvent, RevisionConflict, Source, validate_revision,
    validate_revision_sequence,
)


class StoreError(ValueError):
    """A persisted request cannot be completed."""


class StoreNotFound(StoreError):
    """The object does not exist in the caller's team."""


class StoreConflict(StoreError):
    """The object exists but the request conflicts with current state."""


DRAFT_TARGETS = frozenset({"fresh", "annotation", "brief_text"})
DRAFT_STATUSES = frozenset({"draft", "pending_review", "approved", "rejected", "archived"})


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _actor(user) -> str:
    return getattr(user, "name", None) or getattr(user, "email", None) or "unknown"


def _utc(value: dt.datetime) -> dt.datetime:
    """Normalise SQLite's naive timestamps and PostgreSQL's aware ones for snapshots."""
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _bounded(value: str | None, limit: int, label: str) -> None:
    if value is not None and len(value) > limit:
        raise StoreError(f"{label}超过 {limit} 个字符")


def audit(db: Session, *, team_id: uuid.UUID, user, entity_type: str, entity_id: uuid.UUID | str,
          action: str, revision: int | None = None, reason: str = "",
          details: Mapping[str, Any] | None = None) -> NewsAuditEvent:
    row = NewsAuditEvent(
        team_id=team_id, entity_type=entity_type, entity_id=str(entity_id), action=action,
        revision=revision, actor_id=getattr(user, "id", None), actor_name=_actor(user),
        reason=reason, details=dict(details or {}),
    )
    db.add(row)
    return row


def get_event(db: Session, team_id: uuid.UUID, event_id: uuid.UUID) -> NewsEvent:
    row = db.scalar(select(NewsEvent).where(NewsEvent.id == event_id, NewsEvent.team_id == team_id))
    if row is None:
        raise StoreNotFound("事件不存在")
    return row


def list_events(db: Session, team_id: uuid.UUID, *, limit: int = 50, offset: int = 0) -> list[NewsEvent]:
    return list(db.scalars(select(NewsEvent).where(NewsEvent.team_id == team_id)
                           .order_by(NewsEvent.event_time.desc(), NewsEvent.id)
                           .limit(max(1, min(limit, 100))).offset(max(0, offset))))


def create_event(db: Session, *, team_id: uuid.UUID, user, title: str, event_time: dt.datetime,
                 event_key: str | None = None, payload: Mapping[str, Any] | None = None) -> NewsEvent:
    title = title.strip()
    if not title:
        raise StoreError("事件标题不能为空")
    if event_time.tzinfo is None:
        raise StoreError("event_time 必须带时区")
    if event_key:
        event_key = event_key.strip() or None
        _bounded(event_key, 200, "事件标识")
        if event_key and db.scalar(select(NewsEvent.id).where(
                NewsEvent.team_id == team_id, NewsEvent.event_key == event_key)) is not None:
            raise StoreConflict("同一团队的事件标识已存在")
    row = NewsEvent(team_id=team_id, event_key=event_key, title=title, event_time=event_time,
                    status="open", current_revision=0, payload=dict(payload or {}),
                    created_by=getattr(user, "id", None), created_at=now_utc(), updated_at=now_utc())
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise StoreConflict("同一团队的事件标识已存在") from exc
    audit(db, team_id=team_id, user=user, entity_type="event", entity_id=row.id,
          action="event.create", details={"title": title})
    return row


def get_event_revision(db: Session, team_id: uuid.UUID, event_id: uuid.UUID,
                       number: int) -> NewsEventRevision:
    row = db.scalar(select(NewsEventRevision).where(
        NewsEventRevision.team_id == team_id, NewsEventRevision.event_id == event_id,
        NewsEventRevision.number == number))
    if row is None:
        raise StoreNotFound("事件修订不存在")
    return row


def list_event_revisions(db: Session, team_id: uuid.UUID, event_id: uuid.UUID) -> list[NewsEventRevision]:
    return list(db.scalars(select(NewsEventRevision).where(
        NewsEventRevision.team_id == team_id, NewsEventRevision.event_id == event_id)
        .order_by(NewsEventRevision.number)))


def append_revision(db: Session, *, event: NewsEvent, user, expected_previous: int,
                    sources: Iterable[Source], evidence: Iterable[Evidence],
                    claims: Iterable[Claim], links: Iterable[ClaimEvidenceLink],
                    analyses: Iterable[Analysis], draft: Draft | None, reason: str) -> NewsEventRevision:
    """Append one validated, immutable event revision inside the caller's transaction."""
    if expected_previous < 0:
        raise StoreError("expected_previous 不能为负数")
    reason = reason.strip()
    if not reason:
        raise StoreError("修订必须填写原因")
    number = expected_previous + 1
    domain_event = DomainEvent(id=event.id, team_id=event.team_id, title=event.title,
                               event_time=_utc(event.event_time))
    revision = ArtifactRevision(
        event=domain_event, number=number, sources=tuple(sources), evidence=tuple(evidence),
        claims=tuple(claims), links=tuple(links), analyses=tuple(analyses), draft=draft,
        audit=AuditEvent(event_id=event.id, revision=number, actor=_actor(user),
                         action="create" if expected_previous == 0 else "correct",
                         at=now_utc(), reason=reason),
    )
    for source in revision.sources:
        _bounded(source.id, 200, "来源 ID")
        _bounded(source.original_id, 300, "原始来源 ID")
    for item in revision.evidence:
        _bounded(item.id, 200, "证据 ID")
        _bounded(item.locator, 1024, "证据定位")
    for claim in revision.claims:
        _bounded(claim.id, 200, "Claim ID")
        _bounded(claim.superseded_by, 200, "替代 Claim ID")
    for item in revision.analyses:
        _bounded(item.id, 200, "分析 ID")
    try:
        validate_revision(revision)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc

    latest = db.scalar(select(NewsEventRevision).where(
        NewsEventRevision.event_id == event.id, NewsEventRevision.team_id == event.team_id)
        .order_by(NewsEventRevision.number.desc()).limit(1))
    if latest is None:
        if expected_previous != 0:
            raise StoreConflict("事件尚无修订，expected_previous 应为 0")
    else:
        if latest.number != expected_previous:
            raise StoreConflict("事件已被其他编辑更新，请刷新后重试")
        previous = ArtifactRevision.model_validate(latest.snapshot)
        try:
            validate_revision_sequence(previous, revision)
        except RevisionConflict as exc:
            raise StoreConflict(str(exc)) from exc

    stamp = now_utc()
    result = db.execute(update(NewsEvent).where(
        NewsEvent.id == event.id, NewsEvent.team_id == event.team_id,
        NewsEvent.current_revision == expected_previous,
    ).values(current_revision=number, updated_at=stamp))
    if result.rowcount != 1:
        raise StoreConflict("事件已被其他编辑更新，请刷新后重试")

    snapshot = revision.model_dump(mode="json")
    revision_row = NewsEventRevision(
        id=uuid.uuid4(), team_id=event.team_id, event_id=event.id, number=number,
        snapshot=snapshot, snapshot_sha256=sha256_json(snapshot), audit_reason=reason,
        created_by=getattr(user, "id", None), created_at=stamp,
    )
    db.add(revision_row)
    db.flush()

    source_map = {source.id: source for source in revision.sources}
    evidence_rows: dict[str, NewsEventEvidence] = {}
    for item in revision.evidence:
        source = source_map[item.source_id]
        row = NewsEventEvidence(
            id=uuid.uuid4(), team_id=event.team_id, event_id=event.id, revision_id=revision_row.id,
            evidence_key=item.id, source_key=source.id, source_name=source.name,
            source_kind=source.kind, source_original_id=source.original_id,
            source_published_at=source.published_at, excerpt=item.excerpt,
            captured_at=item.captured_at, locator=item.locator,
            source_snapshot_hash=item.source_snapshot_hash,
            payload=item.model_dump(mode="json"), created_at=stamp,
        )
        db.add(row)
        evidence_rows[item.id] = row

    claim_rows: dict[str, NewsEventClaim] = {}
    for item in revision.claims:
        row = NewsEventClaim(
            id=uuid.uuid4(), team_id=event.team_id, event_id=event.id, revision_id=revision_row.id,
            claim_key=item.id, subject=item.subject, predicate=item.predicate, text=item.text,
            status=item.status.value, numeric=item.numeric.model_dump(mode="json") if item.numeric else None,
            attributed_to=item.attributed_to, superseded_by=item.superseded_by,
            payload=item.model_dump(mode="json"), created_at=stamp,
        )
        db.add(row)
        claim_rows[item.id] = row

    # Flush evidence and claims before the link rows so PostgreSQL enforces the
    # foreign keys in dependency order; SQLAlchemy has no ORM relationship here.
    db.flush()
    for item in revision.links:
        db.add(NewsEventClaimEvidence(
            team_id=event.team_id, event_id=event.id, revision_id=revision_row.id,
            claim_id=claim_rows[item.claim_id].id, evidence_id=evidence_rows[item.evidence_id].id,
            stance=item.stance, created_at=stamp,
        ))
    for item in revision.analyses:
        db.add(NewsEventAnalysis(
            team_id=event.team_id, event_id=event.id, revision_id=revision_row.id,
            analysis_key=item.id, section=item.section, text=item.text, label=item.label,
            claim_ids=list(item.claim_ids), evidence_ids=list(item.evidence_ids),
            payload=item.model_dump(mode="json"), created_at=stamp,
        ))
    audit(db, team_id=event.team_id, user=user, entity_type="event", entity_id=event.id,
          action="event.revision", revision=number, reason=reason,
          details={"snapshot_sha256": revision_row.snapshot_sha256,
                   "claims": len(revision.claims), "evidence": len(revision.evidence)})
    event.current_revision = number
    event.updated_at = stamp
    db.flush()
    return revision_row


def ensure_skill_version(db: Session, *, team_id: uuid.UUID, user, skill_id: str,
                         version: str) -> NewsSkillVersion:
    from app.news_skill_os.manifest import ManifestError, bundled_registry

    _bounded(skill_id, 200, "Skill ID")
    _bounded(version, 30, "Skill 版本")
    try:
        skill = bundled_registry().get(skill_id, version)
    except ManifestError as exc:
        raise StoreError(str(exc)) from exc
    existing = db.scalar(select(NewsSkillVersion).where(
        NewsSkillVersion.team_id == team_id, NewsSkillVersion.skill_id == skill_id,
        NewsSkillVersion.version == version))
    if existing is not None:
        if existing.digest != skill.digest:
            raise StoreConflict("同一 Skill 版本的内容发生变化")
        return existing
    row = NewsSkillVersion(team_id=team_id, skill_id=skill_id, version=version,
                           digest=skill.digest, manifest=skill.manifest, source="bundled",
                           created_by=getattr(user, "id", None), created_at=now_utc())
    db.add(row)
    db.flush()
    return row


def _event_revision_id(db: Session, team_id: uuid.UUID, event_id: uuid.UUID,
                       number: int | None) -> uuid.UUID | None:
    if number is None:
        return None
    row = get_event_revision(db, team_id, event_id, number)
    return row.id


def get_draft(db: Session, team_id: uuid.UUID, draft_id: uuid.UUID) -> NewsDraft:
    row = db.scalar(select(NewsDraft).where(NewsDraft.id == draft_id, NewsDraft.team_id == team_id))
    if row is None:
        raise StoreNotFound("草稿不存在")
    return row


def list_drafts(db: Session, team_id: uuid.UUID, *, event_id: uuid.UUID | None = None,
                limit: int = 50, offset: int = 0) -> list[NewsDraft]:
    statement = select(NewsDraft).where(NewsDraft.team_id == team_id)
    if event_id is not None:
        statement = statement.where(NewsDraft.event_id == event_id)
    return list(db.scalars(statement.order_by(NewsDraft.updated_at.desc(), NewsDraft.id)
                           .limit(max(1, min(limit, 100))).offset(max(0, offset))))


def list_draft_revisions(db: Session, team_id: uuid.UUID, draft_id: uuid.UUID) -> list[NewsDraftRevision]:
    return list(db.scalars(select(NewsDraftRevision).where(
        NewsDraftRevision.team_id == team_id, NewsDraftRevision.draft_id == draft_id)
        .order_by(NewsDraftRevision.number)))


def _validate_draft_input(title: str, body: str, target: str) -> tuple[str, str]:
    title = title.strip()
    if not title:
        raise StoreError("草稿标题不能为空")
    if not body.strip():
        raise StoreError("草稿正文不能为空")
    if target not in DRAFT_TARGETS:
        raise StoreError("草稿目标类型不受支持")
    return title, body


def create_draft(db: Session, *, team_id: uuid.UUID, user, title: str, body: str, target: str = "fresh",
                 event_id: uuid.UUID | None = None, event_revision: int | None = None,
                 skill_id: str | None = None, skill_version: str | None = None,
                 payload: Mapping[str, Any] | None = None) -> NewsDraft:
    title, body = _validate_draft_input(title, body, target)
    event = None
    if event_id is not None:
        event = get_event(db, team_id, event_id)
        if event_revision is not None and not 1 <= event_revision <= event.current_revision:
            raise StoreNotFound("事件修订不存在")
        if event_revision is None:
            event_revision = event.current_revision or None
    elif event_revision is not None:
        raise StoreError("指定事件修订时必须同时指定事件")
    if bool(skill_id) != bool(skill_version):
        raise StoreError("skill_id 与 skill_version 必须同时提供")
    if skill_id:
        ensure_skill_version(db, team_id=team_id, user=user, skill_id=skill_id,
                             version=skill_version or "")
    revision_id = _event_revision_id(db, team_id, event_id, event_revision) if event else None
    stamp = now_utc()
    draft = NewsDraft(id=uuid.uuid4(), team_id=team_id, event_id=event_id, title=title, body=body,
                      target=target, status="draft", current_revision=1, skill_id=skill_id,
                      skill_version=skill_version, payload=dict(payload or {}),
                      created_by=getattr(user, "id", None), created_at=stamp, updated_at=stamp)
    db.add(draft)
    db.add(NewsDraftRevision(
        id=uuid.uuid4(), team_id=team_id, draft_id=draft.id, number=1,
        event_revision_id=revision_id, title=title, body=body, skill_id=skill_id,
        skill_version=skill_version, payload=dict(payload or {}), review_status="pending",
        created_by=getattr(user, "id", None), created_at=stamp,
    ))
    audit(db, team_id=team_id, user=user, entity_type="draft", entity_id=draft.id,
          action="draft.create", revision=1,
          details={"target": target, "event_id": str(event_id) if event_id else None,
                   "skill_id": skill_id, "skill_version": skill_version})
    db.flush()
    return draft


def add_draft_revision(db: Session, *, draft: NewsDraft, user, expected_revision: int, title: str,
                       body: str, event_revision: int | None = None,
                       payload: Mapping[str, Any] | None = None) -> NewsDraftRevision:
    if expected_revision != draft.current_revision:
        raise StoreConflict("草稿已被其他编辑更新，请刷新后重试")
    if draft.status == "archived":
        raise StoreConflict("已归档草稿不能继续修订")
    title, body = _validate_draft_input(title, body, draft.target)
    revision_id = None
    if draft.event_id is not None:
        if event_revision is not None:
            if not 1 <= event_revision <= get_event(db, draft.team_id, draft.event_id).current_revision:
                raise StoreNotFound("事件修订不存在")
            revision_id = _event_revision_id(db, draft.team_id, draft.event_id, event_revision)
        else:
            latest = db.scalar(select(NewsEventRevision).where(
                NewsEventRevision.event_id == draft.event_id,
                NewsEventRevision.team_id == draft.team_id)
                .order_by(NewsEventRevision.number.desc()).limit(1))
            revision_id = latest.id if latest else None
    number = draft.current_revision + 1
    stamp = now_utc()
    row = NewsDraftRevision(
        id=uuid.uuid4(), team_id=draft.team_id, draft_id=draft.id, number=number,
        event_revision_id=revision_id, title=title, body=body, skill_id=draft.skill_id,
        skill_version=draft.skill_version, payload=dict(payload or {}), review_status="pending",
        created_by=getattr(user, "id", None), created_at=stamp,
    )
    result = db.execute(update(NewsDraft).where(
        NewsDraft.id == draft.id, NewsDraft.team_id == draft.team_id,
        NewsDraft.current_revision == expected_revision,
    ).values(current_revision=number, title=title, body=body, status="draft", updated_at=stamp))
    if result.rowcount != 1:
        raise StoreConflict("草稿已被其他编辑更新，请刷新后重试")
    db.add(row)
    draft.title = title
    draft.body = body
    draft.current_revision = number
    draft.status = "draft"
    draft.updated_at = stamp
    audit(db, team_id=draft.team_id, user=user, entity_type="draft", entity_id=draft.id,
          action="draft.revision", revision=number,
          details={"event_revision_id": str(revision_id) if revision_id else None})
    db.flush()
    return row


def submit_draft(db: Session, *, draft: NewsDraft, user) -> NewsDraft:
    if draft.status == "approved":
        raise StoreConflict("草稿已通过审核")
    if draft.status == "archived":
        raise StoreConflict("已归档草稿不能送审")
    draft.status = "pending_review"
    draft.updated_at = now_utc()
    audit(db, team_id=draft.team_id, user=user, entity_type="draft", entity_id=draft.id,
          action="draft.submit", revision=draft.current_revision,
          details={"publication_allowed": False})
    db.flush()
    return draft


def review_draft(db: Session, *, draft: NewsDraft, user, decision: str, note: str = "") -> NewsDraft:
    if decision not in ("approve", "reject"):
        raise StoreError("审核决定必须是 approve 或 reject")
    if draft.status == "approved":
        raise StoreConflict("草稿已通过审核，不能再次审核")
    if draft.status == "archived":
        raise StoreConflict("已归档草稿不能审核")
    if decision == "reject" and not note.strip():
        raise StoreError("驳回必须填写原因")
    revision = db.scalar(select(NewsDraftRevision).where(
        NewsDraftRevision.team_id == draft.team_id, NewsDraftRevision.draft_id == draft.id,
        NewsDraftRevision.number == draft.current_revision))
    if revision is None:
        raise StoreConflict("草稿当前修订不存在")
    stamp = now_utc()
    revision.review_status = "approved" if decision == "approve" else "rejected"
    revision.review_note = note.strip() or None
    revision.reviewed_by = getattr(user, "id", None)
    revision.reviewed_at = stamp
    draft.status = "approved" if decision == "approve" else "rejected"
    draft.updated_at = stamp
    audit(db, team_id=draft.team_id, user=user, entity_type="draft", entity_id=draft.id,
          action="draft.approve" if decision == "approve" else "draft.reject",
          revision=draft.current_revision, reason=note.strip(),
          details={"publication_allowed": False})
    db.flush()
    return draft


def list_audit(db: Session, team_id: uuid.UUID, *, entity_type: str | None = None,
               entity_id: str | None = None, limit: int = 100) -> list[NewsAuditEvent]:
    statement = select(NewsAuditEvent).where(NewsAuditEvent.team_id == team_id)
    if entity_type:
        statement = statement.where(NewsAuditEvent.entity_type == entity_type)
    if entity_id:
        statement = statement.where(NewsAuditEvent.entity_id == entity_id)
    return list(db.scalars(statement.order_by(NewsAuditEvent.created_at.desc())
                           .limit(max(1, min(limit, 200)))))
