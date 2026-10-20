"""Opt-in, team-scoped News Skill OS persistence and preview APIs.

The feature flag remains off by default.  When it is off every route in this
module is a 404 and no new table is read or written.  When it is on, viewers may
read team data, editors may append immutable revisions and submit drafts, and
nothing can be published: there is no CMS/publisher endpoint here.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.briefs.delivery import default_notes
from app.config import get_settings
from app.db.models import BriefConfig
from app.deps import CurrentUser, DbSession, Editor
from app.news_skill_os.annotation_ai import analyze as analyze_annotation
from app.news_skill_os.article_ai import extract as extract_article
from app.news_skill_os.event_workflow import research_input
from app.news_skill_os.fresh_ai import analyze as analyze_fresh_link
from app.news_skill_os.link_ingest import fetch_article
from app.news_skill_os.models import (
    Analysis, Claim, ClaimEvidenceLink, ClaimStatus, Draft, Evidence, NumericFact, Source,
)
from app.news_skill_os.store import (
    StoreConflict, StoreError, StoreNotFound, add_draft_revision, append_revision,
    create_draft, create_event, get_draft, get_event, get_event_revision,
    get_workflow_node, get_workflow_run, list_audit, list_draft_revisions, list_drafts,
    now_utc,
    list_event_revisions, list_events, list_workflow_nodes, list_workflow_runs,
    record_workflow_run, review_draft, submit_draft,
)
from app.news_skill_os.workflow import Workflow, WorkflowEngine, WorkflowError

router = APIRouter(prefix="/api/news-skill-os", tags=["news skill os"])
FIXTURES = frozenset({"official", "rumour", "wrong-number"})
FIXED_BRIEFS = {"volume": "news.fixed-volume", "turnover": "news.fixed-turnover",
                "valuation": "news.fixed-valuation", "liquidity": "news.fixed-liquidity",
                "oil_etf": "news.fixed-oil-etf"}


def _enabled() -> None:
    if not get_settings().news_skill_os_enabled:
        raise HTTPException(status_code=404, detail="News Skill OS 未启用")


@contextmanager
def _errors():
    """Translate store errors without leaking tracebacks or other teams' existence."""
    try:
        yield
    except StoreNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoreConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (StoreError, WorkflowError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    event_time: dt.datetime
    event_key: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_time")
    @classmethod
    def timezone_required(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None:
            raise ValueError("event_time 必须带时区")
        return value


class ArticleCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    event_time: dt.datetime
    event_key: str | None = Field(default=None, max_length=200)
    article: str = Field(min_length=1, max_length=100_000)
    source_name: str = Field(min_length=1, max_length=200)
    source_kind: Literal["official", "named_media", "anonymous"]
    source_url: str | None = Field(default=None, max_length=2048)
    source_original_id: str | None = Field(default=None, max_length=300)

    @field_validator("event_time")
    @classmethod
    def timezone_required(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None:
            raise ValueError("event_time 必须带时区")
        return value


class LinkAnalyze(StrictModel):
    url: str = Field(min_length=8, max_length=2048)
    focus: str = Field(default="", max_length=1000)


class AnnotationCreate(StrictModel):
    text: str = Field(min_length=1, max_length=5000)
    source_url: str | None = Field(default=None, max_length=2048)


class RevisionCreate(StrictModel):
    expected_previous: int = Field(ge=0)
    sources: list[Source] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    links: list[ClaimEvidenceLink] = Field(default_factory=list)
    analyses: list[Analysis] = Field(default_factory=list)
    draft: Draft | None = None
    reason: str = Field(min_length=1, max_length=2000)


class DraftCreate(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)
    target: Literal["fresh", "annotation", "brief_text", "research"] = "fresh"
    event_id: uuid.UUID | None = None
    event_revision: int | None = Field(default=None, ge=1)
    skill_id: str | None = Field(default=None, max_length=200)
    skill_version: str | None = Field(default=None, max_length=30)
    payload: dict[str, Any] = Field(default_factory=dict)


class DraftRevisionCreate(StrictModel):
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100_000)
    event_revision: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class DraftReview(StrictModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)


class WorkflowRunCreate(StrictModel):
    fixture: Literal["official", "rumour", "wrong-number"]


class EventWorkflowRun(StrictModel):
    revision: int | None = Field(default=None, ge=1)


class RunDraftCreate(StrictModel):
    target: Literal["fresh", "annotation"] = "fresh"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _event_out(row) -> dict:
    return {
        "id": str(row.id), "event_key": row.event_key, "title": row.title,
        "event_time": _iso(row.event_time), "status": row.status,
        "current_revision": row.current_revision, "payload": row.payload,
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
    }


def _revision_out(row, *, snapshot: bool = False) -> dict:
    out = {
        "id": str(row.id), "event_id": str(row.event_id), "number": row.number,
        "snapshot_sha256": row.snapshot_sha256, "audit_reason": row.audit_reason,
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": _iso(row.created_at),
    }
    if snapshot:
        out["snapshot"] = row.snapshot
    return out


def _draft_out(row) -> dict:
    return {
        "id": str(row.id), "event_id": str(row.event_id) if row.event_id else None,
        "title": row.title, "body": row.body, "target": row.target, "status": row.status,
        "current_revision": row.current_revision, "skill_id": row.skill_id,
        "skill_version": row.skill_version, "payload": row.payload,
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
        "publication_allowed": False,
    }


def _draft_revision_out(row) -> dict:
    return {
        "id": str(row.id), "draft_id": str(row.draft_id), "number": row.number,
        "event_revision_id": str(row.event_revision_id) if row.event_revision_id else None,
        "title": row.title, "body": row.body, "skill_id": row.skill_id,
        "skill_version": row.skill_version, "payload": row.payload,
        "review_status": row.review_status, "review_note": row.review_note,
        "reviewed_by": str(row.reviewed_by) if row.reviewed_by else None,
        "reviewed_at": _iso(row.reviewed_at),
        "created_by": str(row.created_by) if row.created_by else None,
        "created_at": _iso(row.created_at),
    }


def _run_out(row, *, detail: bool = False) -> dict:
    out = {
        "id": str(row.id), "run_key": row.run_key, "event_id": str(row.event_id) if row.event_id else None,
        "event_revision": row.event_revision,
        "workflow_id": row.workflow_id, "workflow_digest": row.workflow_digest,
        "status": row.status, "input_digest": row.input_digest,
        "error": row.error, "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at), "created_at": _iso(row.created_at),
        "publication_allowed": False,
    }
    if detail:
        out["input"] = row.input
        out["output"] = row.output
    return out


def _node_out(row) -> dict:
    return {
        "id": str(row.id), "run_id": str(row.run_id), "node_id": row.node_id,
        "skill_id": row.skill_id, "skill_version": row.skill_version, "status": row.status,
        "input_digest": row.input_digest, "output_digest": row.output_digest,
        "source_snapshot_hashes": row.source_snapshot_hashes,
        "duration_ms": row.duration_ms, "attempts": row.attempts, "cached": row.cached,
        "error": row.error, "output": row.output, "created_at": _iso(row.created_at),
    }


@router.get("/skills")
def skills(user: CurrentUser):
    """List the bundled, locked manifests available to this deployment."""
    _enabled()
    from app.news_skill_os.manifest import bundled_registry

    with _errors():
        registry = bundled_registry()
        items = []
        for skill_id, version in registry.keys():
            manifest = registry.get(skill_id, version).manifest
            items.append({"id": skill_id, "version": version, "owner": manifest.get("owner"),
                          "description": manifest.get("description"),
                          "allowed_tools": manifest.get("allowed_tools", [])})
        return {"skills": sorted(items, key=lambda item: (item["id"], item["version"]))}


@router.get("/events")
def events(db: DbSession, user: CurrentUser, limit: int = Query(50, ge=1, le=100),
           offset: int = Query(0, ge=0)):
    _enabled()
    with _errors():
        return {"items": [_event_out(row) for row in list_events(db, user.team_id, limit=limit, offset=offset)]}


@router.post("/events", status_code=201)
def create_event_route(payload: EventCreate, db: DbSession, user: Editor):
    _enabled()
    with _errors():
        row = create_event(db, team_id=user.team_id, user=user, title=payload.title,
                           event_time=payload.event_time, event_key=payload.event_key,
                           payload=payload.payload)
        return _event_out(row)


@router.post("/events/from-article", status_code=201)
def create_event_from_article(body: ArticleCreate, db: DbSession, user: Editor):
    """Extract claim proposals from a pasted article and persist them unverified."""
    _enabled()
    with _errors():
        from app.db.models import Team
        team = db.get(Team, user.team_id)
        extracted = extract_article(team, body.title.strip(), body.article,
                                    body.source_name.strip(), body.source_kind)
        source = Source(
            id="s1", name=body.source_name.strip(), kind=body.source_kind,
            url=body.source_url,
            original_id=(body.source_original_id or body.source_url or body.source_name).strip()[:300],
        )
        stamp = now_utc()
        evidence = []
        claims = []
        links = []
        for index, item in enumerate(extracted["claims"], start=1):
            evidence_id = f"e{index}"
            claim_id = item["id"]
            numeric = None
            if isinstance(item.get("numeric"), dict):
                try:
                    numeric = NumericFact.model_validate(item["numeric"])
                except ValueError:
                    numeric = None
            evidence.append(Evidence(id=evidence_id, source_id=source.id,
                                     excerpt=item["evidence"], captured_at=stamp,
                                     locator=body.source_url))
            claims.append(Claim(id=claim_id, subject=item["subject"], predicate=item["predicate"],
                                text=item["text"], status=ClaimStatus.UNVERIFIED,
                                numeric=numeric))
            links.append(ClaimEvidenceLink(claim_id=claim_id, evidence_id=evidence_id, stance="supports"))
        analyses = [
            Analysis(id=item["id"], section=item["section"], text=item["text"], label=item["label"],
                     claim_ids=tuple(item["claim_ids"]), evidence_ids=tuple(item["evidence_ids"]))
            for item in extracted.get("analyses", [])
        ]
        event = create_event(db, team_id=user.team_id, user=user, title=extracted["title"],
                             event_time=body.event_time, event_key=body.event_key,
                             payload={"ingest": "article", "source_name": body.source_name})
        revision = append_revision(
            db, event=event, user=user, expected_previous=0, sources=[source],
            evidence=evidence, claims=claims, links=links, analyses=analyses, draft=None,
            reason=f"AI 从新闻稿提取 {len(claims)} 条待核查断言",
        )
        return {"event": _event_out(event), "revision": _revision_out(revision),
                "claim_count": len(claims), "evidence_count": len(evidence),
                "human_approval_required": True, "publication_allowed": False}


@router.post("/annotations/analyze")
def analyze_annotation_route(body: AnnotationCreate, db: DbSession, user: Editor):
    """Generate a plain three-part annotation for one news flash; does not save."""
    _enabled()
    with _errors():
        from app.db.models import Team
        return analyze_annotation(db.get(Team, user.team_id), body.text,
                                  body.source_url or "")


@router.post("/from-link/analyze", status_code=201)
def analyze_link(body: LinkAnalyze, db: DbSession, user: Editor):
    """Fetch a public link, analyze it and store unverified claims for review."""
    _enabled()
    with _errors():
        from app.db.models import Team
        article = fetch_article(body.url)
        team = db.get(Team, user.team_id)
        result = analyze_fresh_link(team, article, body.focus)
        result["analysis"]["sources"] = result["sources"]
        source = Source(id="s1", name=(article.get("site") or "新闻链接")[:200],
                        kind="named_media", url=article["url"], original_id=article["url"][:300])
        stamp = now_utc()
        evidence, claims, links = [], [], []
        for index, item in enumerate(result["claims"], start=1):
            evidence_id = f"e{index}"
            evidence.append(Evidence(id=evidence_id, source_id=source.id,
                                     excerpt=item["evidence"], captured_at=stamp,
                                     locator=article["url"]))
            claims.append(Claim(id=item["id"], subject=item["subject"], predicate=item["predicate"],
                                text=item["text"], status=ClaimStatus.UNVERIFIED))
            links.append(ClaimEvidenceLink(claim_id=item["id"], evidence_id=evidence_id, stance="supports"))
        published = None
        if article.get("published_at"):
            try:
                published = dt.datetime.fromisoformat(article["published_at"].replace("Z", "+00:00"))
            except ValueError:
                published = None
        event = create_event(db, team_id=user.team_id, user=user, title=result["event_title"],
                             event_time=published or stamp,
                             payload={**result["analysis"], "source_url": article["url"],
                                      "site": article.get("site", ""), "focus": body.focus,
                                      "ingest": "link"})
        revision = append_revision(
            db, event=event, user=user, expected_previous=0, sources=[source],
            evidence=evidence, claims=claims, links=links, analyses=[], draft=None,
            reason="AI 从新闻链接提取待核查断言",
        )
        return {"event": _event_out(event), "revision": _revision_out(revision),
                "analysis": result["analysis"], "claims": result["claims"],
                "sources": result["sources"], "claim_count": len(claims),
                "publication_allowed": False, "human_approval_required": True}


@router.post("/events/{event_id}/fresh-draft", status_code=201)
def fresh_draft(event_id: uuid.UUID, db: DbSession, user: Editor):
    _enabled()
    with _errors():
        event = get_event(db, user.team_id, event_id)
        revisions = list_event_revisions(db, user.team_id, event_id)
        if not revisions:
            raise StoreConflict("事件尚无分析修订")
        analysis = (event.payload or {}).get("fresh_analysis") if isinstance(event.payload, dict) else None
        if not analysis:
            analysis = event.payload
        if not isinstance(analysis, dict) or not analysis.get("body") or not analysis.get("title"):
            raise StoreError("该事件没有可用的 Plus新鲜事分析")
        body = analysis["body"] + "\n\n" + analysis.get("notes", "")
        draft = create_draft(
            db, team_id=user.team_id, user=user, title=analysis["title"], body=body,
            target="fresh", event_id=event.id, event_revision=revisions[-1].number,
            payload={"source_url": (event.payload or {}).get("source_url"),
                     "analysis": analysis, "claim_count": len(revisions[-1].snapshot.get("claims", []))},
        )
        return _draft_out(draft)


@router.get("/events/{event_id}")
def event_detail(event_id: uuid.UUID, db: DbSession, user: CurrentUser):
    _enabled()
    with _errors():
        event = get_event(db, user.team_id, event_id)
        return {
            "event": _event_out(event),
            "revisions": [_revision_out(row) for row in list_event_revisions(db, user.team_id, event_id)],
            "drafts": [_draft_out(row) for row in list_drafts(db, user.team_id, event_id=event_id)],
        }


@router.get("/events/{event_id}/revisions/{number}")
def event_revision(event_id: uuid.UUID, number: int, db: DbSession, user: CurrentUser):
    _enabled()
    if number < 1:
        raise HTTPException(status_code=422, detail="修订号必须从 1 开始")
    with _errors():
        return _revision_out(get_event_revision(db, user.team_id, event_id, number), snapshot=True)


@router.post("/events/{event_id}/revisions", status_code=201)
def append_event_revision(event_id: uuid.UUID, payload: RevisionCreate, db: DbSession, user: Editor):
    _enabled()
    with _errors():
        event = get_event(db, user.team_id, event_id)
        row = append_revision(
            db, event=event, user=user, expected_previous=payload.expected_previous,
            sources=payload.sources, evidence=payload.evidence, claims=payload.claims,
            links=payload.links, analyses=payload.analyses, draft=payload.draft,
            reason=payload.reason,
        )
        return _revision_out(row)


@router.post("/events/{event_id}/workflows/research/runs", status_code=201)
def run_event_research(event_id: uuid.UUID, payload: EventWorkflowRun, db: DbSession, user: Editor):
    """Run the research DAG over a persisted revision; record only, never publish."""
    _enabled()
    with _errors():
        from app.news_skill_os.manifest import bundled_registry

        event = get_event(db, user.team_id, event_id)
        if payload.revision is not None:
            event_revision = get_event_revision(db, user.team_id, event_id, payload.revision)
        else:
            revisions = list_event_revisions(db, user.team_id, event_id)
            if not revisions:
                raise StoreConflict("事件尚无修订，不能运行工作流")
            event_revision = revisions[-1]
        data = research_input(event_revision.snapshot)
        root = Path(str(files("app.news_skill_os")))
        registry = bundled_registry()
        workflow = Workflow.load_yaml(root / "manifests" / "research-workflow.yaml", registry)
        result = WorkflowEngine(registry).run(workflow, data)
        run = record_workflow_run(db, team_id=user.team_id, user=user, workflow=workflow,
                                  payload=data, result=result, event_id=event.id,
                                  event_revision=event_revision.number)
        return {"run": _run_out(run, detail=True), "result": asdict(result),
                "publication_allowed": False, "human_approval_required": True}


@router.get("/drafts")
def drafts(db: DbSession, user: CurrentUser, event_id: uuid.UUID | None = None,
           limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    _enabled()
    with _errors():
        return {"items": [_draft_out(row) for row in list_drafts(
            db, user.team_id, event_id=event_id, limit=limit, offset=offset)]}


@router.post("/drafts", status_code=201)
def create_draft_route(payload: DraftCreate, db: DbSession, user: Editor):
    _enabled()
    with _errors():
        row = create_draft(db, team_id=user.team_id, user=user, title=payload.title,
                           body=payload.body, target=payload.target, event_id=payload.event_id,
                           event_revision=payload.event_revision, skill_id=payload.skill_id,
                           skill_version=payload.skill_version, payload=payload.payload)
        return _draft_out(row)


@router.get("/drafts/{draft_id}")
def draft_detail(draft_id: uuid.UUID, db: DbSession, user: CurrentUser):
    _enabled()
    with _errors():
        draft = get_draft(db, user.team_id, draft_id)
        return {"draft": _draft_out(draft),
                "revisions": [_draft_revision_out(row) for row in
                              list_draft_revisions(db, user.team_id, draft_id)]}


@router.post("/drafts/{draft_id}/revisions", status_code=201)
def create_draft_revision(draft_id: uuid.UUID, payload: DraftRevisionCreate, db: DbSession, user: Editor):
    _enabled()
    with _errors():
        draft = get_draft(db, user.team_id, draft_id)
        row = add_draft_revision(db, draft=draft, user=user,
                                 expected_revision=payload.expected_revision,
                                 title=payload.title, body=payload.body,
                                 event_revision=payload.event_revision, payload=payload.payload)
        return _draft_revision_out(row)


@router.post("/drafts/{draft_id}/submit")
def submit_draft_route(draft_id: uuid.UUID, db: DbSession, user: Editor):
    _enabled()
    with _errors():
        return _draft_out(submit_draft(db, draft=get_draft(db, user.team_id, draft_id), user=user))


@router.post("/drafts/{draft_id}/review")
def review_draft_route(draft_id: uuid.UUID, payload: DraftReview, db: DbSession, user: Editor):
    _enabled()
    with _errors():
        return _draft_out(review_draft(db, draft=get_draft(db, user.team_id, draft_id),
                                       user=user, decision=payload.decision, note=payload.note))


@router.get("/audit")
def audit_route(db: DbSession, user: CurrentUser, entity_type: str | None = None,
                entity_id: str | None = None, limit: int = Query(100, ge=1, le=200)):
    _enabled()
    with _errors():
        rows = list_audit(db, user.team_id, entity_type=entity_type, entity_id=entity_id, limit=limit)
        return {"items": [{
            "id": str(row.id), "entity_type": row.entity_type, "entity_id": row.entity_id,
            "action": row.action, "revision": row.revision, "actor_name": row.actor_name,
            "reason": row.reason, "details": row.details, "created_at": _iso(row.created_at),
        } for row in rows]}


@router.get("/workflow-runs")
def workflow_runs(db: DbSession, user: CurrentUser, limit: int = Query(50, ge=1, le=100),
                  offset: int = Query(0, ge=0)):
    _enabled()
    with _errors():
        return {"items": [_run_out(row) for row in list_workflow_runs(
            db, user.team_id, limit=limit, offset=offset)]}


@router.get("/workflow-runs/{run_id}")
def workflow_run_detail(run_id: uuid.UUID, db: DbSession, user: CurrentUser):
    _enabled()
    with _errors():
        run = get_workflow_run(db, user.team_id, run_id)
        return {"run": _run_out(run, detail=True),
                "nodes": [_node_out(row) for row in list_workflow_nodes(db, user.team_id, run_id)]}


@router.post("/workflow-runs/{run_id}/drafts", status_code=201)
def draft_from_run(run_id: uuid.UUID, payload: RunDraftCreate, db: DbSession, user: Editor):
    """Materialise a reviewable draft from a persisted analysis node; never publish."""
    _enabled()
    with _errors():
        run = get_workflow_run(db, user.team_id, run_id)
        if run.status != "succeeded":
            raise StoreConflict("只有成功完成的工作流才能生成草稿")
        if run.event_id is None or run.event_revision is None:
            raise StoreError("该运行未绑定事件修订，不能生成草稿")
        analysis = (run.output or {}).get("analysis")
        if not isinstance(analysis, dict):
            raise StoreError("工作流输出中没有分析节点结果")
        node = get_workflow_node(db, user.team_id, run.id, "analysis")
        event = get_event(db, user.team_id, run.event_id)
        if payload.target == "fresh":
            generated = analysis.get("draft") or {}
            title, body = generated.get("title", ""), generated.get("body", "")
        else:
            sections = analysis.get("sections") or {}
            labels = ("前因", "后果", "补充说明")
            if any(not isinstance(sections.get(label), str) or not sections[label].strip()
                   for label in labels):
                raise StoreError("三段分析不完整，不能生成注解草稿")
            title = f"{event.title}·注解"[:300]
            body = "\n\n".join(f"【{label}】\n{sections[label].strip()}" for label in labels)
        draft = create_draft(
            db, team_id=user.team_id, user=user, title=title, body=body, target=payload.target,
            event_id=run.event_id, event_revision=run.event_revision,
            skill_id=node.skill_id, skill_version=node.skill_version,
            payload={"workflow_run_id": str(run.id), "node_id": node.node_id,
                     "claim_ids": analysis.get("claim_ids", []),
                     "evidence_ids": analysis.get("evidence_ids", []),
                     "confidence": analysis.get("confidence"),
                     "unresolved": analysis.get("unresolved", [])},
        )
        return _draft_out(draft)


@router.post("/workflows/research/runs", status_code=201)
def run_research_workflow(payload: WorkflowRunCreate, db: DbSession, user: Editor):
    """Run a bundled offline fixture and persist the DAG record; never publish."""
    _enabled()
    with _errors():
        from app.news_skill_os.manifest import bundled_registry

        root = Path(str(files("app.news_skill_os")))
        data = json.loads((root / "fixtures" / f"{payload.fixture}.json").read_text(encoding="utf-8"))
        registry = bundled_registry()
        workflow = Workflow.load_yaml(root / "manifests" / "research-workflow.yaml", registry)
        result = WorkflowEngine(registry).run(workflow, data)
        run = record_workflow_run(db, team_id=user.team_id, user=user, workflow=workflow,
                                  payload=data, result=result)
        return {"run": _run_out(run, detail=True), "result": asdict(result),
                "publication_allowed": False, "human_approval_required": True}


@router.post("/fixtures/{name}/run")
def run_fixture(name: str, user: Editor):
    _enabled()
    if name not in FIXTURES:
        raise HTTPException(status_code=404, detail="样例不存在")
    from app.news_skill_os.manifest import bundled_registry

    root = Path(str(files("app.news_skill_os")))
    payload = json.loads((root / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
    registry = bundled_registry()
    workflow = Workflow.load_yaml(root / "manifests" / "research-workflow.yaml", registry)
    result = WorkflowEngine(registry).run(workflow, payload)
    return {"result": asdict(result), "publication_allowed": False,
            "human_approval_required": True, "requested_by": str(user.id)}


@router.post("/fixed-briefs/{category}/preview")
def preview_fixed_brief(category: str, payload: dict, db: DbSession, user: Editor):
    """Text-only preview from supplied verified facts and the team's current note."""
    _enabled()
    if category not in FIXED_BRIEFS:
        raise HTTPException(status_code=404, detail="固定快讯类别不存在")
    if set(payload) != {"data_date", "facts"}:
        raise HTTPException(status_code=422, detail="仅接受 data_date 和 facts；固定解释由团队设置读取")
    from app.news_skill_os.manifest import ManifestError, bundled_registry

    config = db.get(BriefConfig, user.team_id)
    note = (config.notes or {}).get(category, default_notes(category)) if config else default_notes(category)
    skill = bundled_registry().get(FIXED_BRIEFS[category], "0.1.0")
    try:
        result = skill.run({**payload, "fixed_note": note})
    except (ManifestError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"result": result, "skill_id": skill.key[0], "skill_version": skill.key[1],
            "publication_allowed": False, "human_approval_required": True,
            "requested_by": str(user.id)}
