"""Versioned, source-attributed news facts; no persistence or network access."""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    CORROBORATED = "corroborated"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Source(FrozenModel):
    id: str
    name: str
    url: str | None = None
    kind: Literal["official", "named_media", "anonymous", "local_fixture"]
    # Syndicated stories keep the same original_id; they are one source root.
    original_id: str
    published_at: dt.datetime | None = None


class NumericFact(FrozenModel):
    value: float = Field(allow_inf_nan=False)
    kind: Literal["money", "ratio", "count", "price", "rate"]
    unit: str
    currency: str | None = None
    comparison: Literal["level", "yoy", "mom", "qoq", "change"] = "level"
    value_type: Literal["actual", "forecast", "estimate"] = "actual"
    period_type: Literal["calendar", "fiscal", "instant"] = "instant"
    period_start: dt.date | None = None
    period_end: dt.date | None = None

    @model_validator(mode="after")
    def check_units(self) -> NumericFact:
        if self.kind == "money" and not self.currency:
            raise ValueError("money requires currency")
        if self.kind in ("ratio", "rate") and self.unit not in ("%", "bps", "倍"):
            raise ValueError("ratio/rate requires an explicit %, bps or 倍 unit")
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("period_start and period_end must be supplied together")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start is after period_end")
        return self


class Evidence(FrozenModel):
    id: str
    source_id: str
    excerpt: str
    captured_at: dt.datetime
    locator: str | None = None
    source_snapshot_hash: str | None = None


class Claim(FrozenModel):
    id: str
    subject: str
    predicate: str
    text: str
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    numeric: NumericFact | None = None
    attributed_to: str | None = None
    superseded_by: str | None = None


class ClaimEvidenceLink(FrozenModel):
    claim_id: str
    evidence_id: str
    stance: Literal["supports", "contradicts"]


class Analysis(FrozenModel):
    id: str
    section: Literal["前因", "后果", "补充说明"]
    text: str
    label: Literal["fact", "inference"]
    claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class Draft(FrozenModel):
    title: str
    body: str
    review_status: Literal["pending", "passed", "failed"] = "pending"
    approved_by: str | None = None


class NewsEvent(FrozenModel):
    id: uuid.UUID
    team_id: uuid.UUID
    title: str
    event_time: dt.datetime


class AuditEvent(FrozenModel):
    event_id: uuid.UUID
    revision: int
    actor: str
    action: str
    at: dt.datetime
    reason: str


class ArtifactRevision(FrozenModel):
    event: NewsEvent
    number: int
    sources: tuple[Source, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    claims: tuple[Claim, ...] = ()
    links: tuple[ClaimEvidenceLink, ...] = ()
    analyses: tuple[Analysis, ...] = ()
    draft: Draft | None = None
    audit: AuditEvent


class RevisionConflict(ValueError):
    pass


def validate_revision(revision: ArtifactRevision) -> None:
    sources = {item.id: item for item in revision.sources}
    evidence = {item.id: item for item in revision.evidence}
    claims = {item.id: item for item in revision.claims}
    if len(sources) != len(revision.sources) or len(evidence) != len(revision.evidence) or len(claims) != len(revision.claims):
        raise ValueError("duplicate source, evidence or claim ID")
    if any(item.source_id not in sources for item in revision.evidence):
        raise ValueError("evidence refers to missing source")
    if any(link.claim_id not in claims or link.evidence_id not in evidence for link in revision.links):
        raise ValueError("claim-evidence link refers to missing ID")
    for item in revision.analyses:
        if not item.claim_ids and not item.evidence_ids:
            raise ValueError("analysis requires a fact dependency")
        if any(ref not in claims for ref in item.claim_ids) or any(ref not in evidence for ref in item.evidence_ids):
            raise ValueError("analysis refers to missing fact or evidence")
    for claim in revision.claims:
        roots = {
            sources[evidence[link.evidence_id].source_id].original_id
            for link in revision.links if link.claim_id == claim.id and link.stance == "supports"
        }
        contradictions = any(link.claim_id == claim.id and link.stance == "contradicts" for link in revision.links)
        official = any(
            sources[evidence[link.evidence_id].source_id].kind == "official"
            for link in revision.links if link.claim_id == claim.id and link.stance == "supports"
        )
        if claim.status == ClaimStatus.VERIFIED and (contradictions or not (official or len(roots) >= 2)):
            raise ValueError(f"claim {claim.id}: verified requires official or two independent source roots without contradiction")
        if claim.status == ClaimStatus.CORROBORATED and len(roots) < 2:
            raise ValueError(f"claim {claim.id}: corroborated requires two independent source roots")
        if claim.status == ClaimStatus.DISPUTED and not contradictions:
            raise ValueError(f"claim {claim.id}: disputed requires contradictory evidence")
        if claim.status == ClaimStatus.SUPERSEDED and claim.superseded_by not in claims:
            raise ValueError(f"claim {claim.id}: superseded requires replacement claim")
    # Two verified versions of the same measured quantity cannot silently coexist.
    measured: dict[tuple, NumericFact] = {}
    for claim in revision.claims:
        if claim.status != ClaimStatus.VERIFIED or claim.numeric is None:
            continue
        fact = claim.numeric
        key = (claim.subject, claim.predicate, fact.kind, fact.currency, fact.comparison,
               fact.value_type, fact.period_type, fact.period_start, fact.period_end)
        old = measured.get(key)
        if old and (old.value != fact.value or old.unit != fact.unit):
            raise ValueError("conflicting verified numeric claims")
        measured[key] = fact


CLAIM_TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]] = {
    ClaimStatus.UNVERIFIED: {ClaimStatus.CORROBORATED, ClaimStatus.VERIFIED, ClaimStatus.DISPUTED,
                             ClaimStatus.REJECTED, ClaimStatus.SUPERSEDED},
    ClaimStatus.CORROBORATED: {ClaimStatus.VERIFIED, ClaimStatus.DISPUTED, ClaimStatus.REJECTED,
                               ClaimStatus.SUPERSEDED},
    ClaimStatus.VERIFIED: {ClaimStatus.DISPUTED, ClaimStatus.REJECTED, ClaimStatus.SUPERSEDED},
    ClaimStatus.DISPUTED: {ClaimStatus.VERIFIED, ClaimStatus.REJECTED, ClaimStatus.SUPERSEDED},
    ClaimStatus.REJECTED: {ClaimStatus.SUPERSEDED},
    ClaimStatus.SUPERSEDED: set(),
}


def validate_revision_sequence(previous: ArtifactRevision | None, current: ArtifactRevision) -> None:
    """Check append-only history rules shared by memory and database ledgers.

    A correction must preserve every earlier claim ID, may only move through the
    declared status graph, and must state why the revision exists.  The event
    identity is immutable for the life of the ledger.
    """
    if previous is None:
        return
    if previous.event != current.event:
        raise RevisionConflict("event metadata changed across revisions")
    old_claims = {claim.id: claim for claim in previous.claims}
    new_claims = {claim.id: claim for claim in current.claims}
    if not old_claims.keys() <= new_claims.keys():
        raise RevisionConflict("claims cannot disappear from revision history")
    for claim_id, old in old_claims.items():
        new = new_claims[claim_id]
        if (old.subject, old.predicate, old.text, old.numeric, old.attributed_to) != (
            new.subject, new.predicate, new.text, new.numeric, new.attributed_to
        ):
            raise RevisionConflict("fact correction requires a new claim ID")
        if old.status != new.status and new.status not in CLAIM_TRANSITIONS[old.status]:
            raise RevisionConflict(f"invalid claim status transition: {old.status} → {new.status}")
    if not current.audit.reason.strip():
        raise RevisionConflict("revision requires an audit reason")


class EventLedger:
    """Thread-safe, append-only in-memory revisions for offline validation."""

    def __init__(self) -> None:
        self._revisions: dict[tuple[uuid.UUID, uuid.UUID], list[ArtifactRevision]] = {}
        self._lock = threading.RLock()

    def append(self, revision: ArtifactRevision, *, expected_previous: int) -> ArtifactRevision:
        validate_revision(revision)
        key = (revision.event.team_id, revision.event.id)
        with self._lock:
            history = self._revisions.setdefault(key, [])
            if len(history) != expected_previous or revision.number != expected_previous + 1:
                raise RevisionConflict("concurrent or out-of-order revision")
            validate_revision_sequence(history[-1] if history else None, revision)
            history.append(revision)
            return revision

    def get(self, team_id: uuid.UUID, event_id: uuid.UUID, number: int | None = None) -> ArtifactRevision:
        with self._lock:
            history = self._revisions.get((team_id, event_id), [])
            if not history or (number is not None and (number < 1 or number > len(history))):
                raise KeyError("event revision not found in team")
            return history[-1] if number is None else history[number - 1]

    def history(self, team_id: uuid.UUID, event_id: uuid.UUID) -> tuple[ArtifactRevision, ...]:
        with self._lock:
            return tuple(self._revisions.get((team_id, event_id), []))
