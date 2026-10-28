"""Opt-in, team-scoped News Skill OS persistence and preview APIs.

The feature flag remains off by default.  When it is off every route in this
module is a 404 and no new table is read or written.  When it is on, viewers may
read team data, editors may append immutable revisions and submit drafts, and
nothing can be published: there is no CMS/publisher endpoint here.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.briefs.delivery import default_notes
from app.config import get_settings
from app.db.models import BriefConfig
from app.deps import CurrentUser, DbSession, Editor
from app.news_skill_os.annotation_ai import analyze as analyze_annotation
from app.news_skill_os.article_ai import extract as extract_article
from app.news_skill_os.fresh_ai import analyze as analyze_fresh_link
from app.news_skill_os.fresh_research import research as research_fresh_link
from app.news_skill_os.link_ingest import fetch_article
from app.news_skill_os.models import (
    Analysis, Claim, ClaimEvidenceLink, ClaimStatus, Draft, Evidence, NumericFact, Source,
)
from app.news_skill_os.store import (
    StoreConflict, StoreError, StoreNotFound, add_draft_revision, append_revision,
    create_draft, create_event, get_draft, get_event, get_event_revision,
    list_audit, list_draft_revisions, list_drafts,
    now_utc,
    list_event_revisions, list_events, review_draft, submit_draft,
)

router = APIRouter(prefix="/api/news-skill-os", tags=["news skill os"])
logger = logging.getLogger(__name__)
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
    except (StoreError, ValueError, KeyError, TypeError) as exc:
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
    target: Literal["fresh", "annotation", "brief_text"] = "fresh"
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
        try:
            research = research_fresh_link(team, article, result["claims"])
        except Exception:
            logger.exception("Plus新鲜事外部检索失败")
            research = {"status": "unavailable", "queries": [], "sources": [],
                        "matches": [], "checks": [],
                        "warnings": ["外部检索暂不可用；本次仅完成原文分析，请人工核查。"]}
        result["analysis"]["external_research"] = {
            "status": research["status"], "queries": research["queries"],
            "sources": research["sources"], "matches": research["matches"],
            "checks": research["checks"], "warnings": research["warnings"],
        }
        result["analysis"]["warnings"] = [*result["analysis"].get("warnings", []),
                                           *research["warnings"]]
        result["sources"] = list(dict.fromkeys([
            *result["sources"], *(item["url"] for item in research["sources"]),
        ]))
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
        external_sources = {item["id"]: item for item in research["sources"]}
        sources = [source] + [Source(id=item["id"], name=item["site"],
                                     kind="named_media", url=item["url"],
                                     original_id=item["host"][:300])
                              for item in external_sources.values()]
        for index, item in enumerate(research["matches"], start=len(evidence) + 1):
            evidence_id = f"e{index}"
            evidence.append(Evidence(id=evidence_id, source_id=item["source_id"],
                                     excerpt=item["quote"], captured_at=stamp,
                                     locator=external_sources[item["source_id"]]["url"]))
            links.append(ClaimEvidenceLink(claim_id=item["claim_id"],
                                           evidence_id=evidence_id, stance=item["stance"]))
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
            db, event=event, user=user, expected_previous=0, sources=sources,
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
