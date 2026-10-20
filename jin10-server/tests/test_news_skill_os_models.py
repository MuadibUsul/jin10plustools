"""Offline evidence and revision checks; these tests never read the network."""

import datetime as dt
import uuid

import pytest

from app.news_skill_os.models import (
    Analysis, ArtifactRevision, AuditEvent, Claim, ClaimEvidenceLink, ClaimStatus,
    EventLedger, Evidence, NewsEvent, NumericFact, RevisionConflict, Source, validate_revision,
)


NOW = dt.datetime(2026, 10, 20, tzinfo=dt.UTC)


def sample(*, source_kind="official", original="origin-1", status=ClaimStatus.UNVERIFIED, value=100.0):
    event = NewsEvent(id=uuid.uuid4(), team_id=uuid.uuid4(), title="Sample", event_time=NOW)
    source = Source(id="s1", name="Primary", kind=source_kind, original_id=original)
    evidence = Evidence(id="e1", source_id="s1", excerpt="Reported value 100", captured_at=NOW)
    claim = Claim(id="c1", subject="Issuer", predicate="revenue", text="Revenue 100 USD",
                  status=status, numeric=NumericFact(value=value, kind="money", unit="million", currency="USD"))
    audit = AuditEvent(event_id=event.id, revision=1, actor="editor", action="create", at=NOW, reason="fixture")
    return ArtifactRevision(event=event, number=1, sources=(source,), evidence=(evidence,), claims=(claim,),
                            links=(ClaimEvidenceLink(claim_id="c1", evidence_id="e1", stance="supports"),), audit=audit)


def test_official_evidence_can_verify_but_anonymous_cannot():
    validate_revision(sample(status=ClaimStatus.VERIFIED))
    with pytest.raises(ValueError, match="two independent"):
        validate_revision(sample(source_kind="anonymous", status=ClaimStatus.VERIFIED))


def test_reprints_are_not_independent_sources():
    revision = sample(source_kind="named_media", status=ClaimStatus.CORROBORATED)
    source2 = Source(id="s2", name="Reprint", kind="named_media", original_id="origin-1")
    evidence2 = Evidence(id="e2", source_id="s2", excerpt="Reprinted report", captured_at=NOW)
    copied = revision.model_copy(update={
        "sources": revision.sources + (source2,), "evidence": revision.evidence + (evidence2,),
        "links": revision.links + (ClaimEvidenceLink(claim_id="c1", evidence_id="e2", stance="supports"),),
    })
    with pytest.raises(ValueError, match="two independent"):
        validate_revision(copied)
    independent = copied.model_copy(update={"sources": (revision.sources[0], source2.model_copy(update={"original_id": "origin-2"}))})
    validate_revision(independent)


def test_numeric_conflict_requires_explicit_resolution():
    revision = sample(status=ClaimStatus.VERIFIED)
    conflict = Claim(id="c2", subject="Issuer", predicate="revenue", text="Revenue 200 USD",
                     status=ClaimStatus.VERIFIED,
                     numeric=NumericFact(value=200, kind="money", unit="million", currency="USD"))
    with pytest.raises(ValueError, match="conflicting verified"):
        validate_revision(revision.model_copy(update={"claims": revision.claims + (conflict,),
            "links": revision.links + (ClaimEvidenceLink(claim_id="c2", evidence_id="e1", stance="supports"),)}))


def test_fact_retraction_is_a_new_revision_and_old_snapshot_remains():
    first = sample(status=ClaimStatus.VERIFIED)
    ledger = EventLedger()
    ledger.append(first, expected_previous=0)
    replacement = Claim(id="c2", subject="Issuer", predicate="revenue", text="Revised value",
                        status=ClaimStatus.UNVERIFIED,
                        numeric=NumericFact(value=80, kind="money", unit="million", currency="USD"))
    superseded = first.claims[0].model_copy(update={"status": ClaimStatus.SUPERSEDED, "superseded_by": "c2"})
    second = first.model_copy(update={"number": 2, "claims": (superseded, replacement),
        "audit": first.audit.model_copy(update={"revision": 2, "action": "correct", "reason": "official correction"})})
    ledger.append(second, expected_previous=1)
    assert ledger.get(first.event.team_id, first.event.id, 1).claims[0].status == ClaimStatus.VERIFIED
    assert ledger.get(first.event.team_id, first.event.id).claims[0].status == ClaimStatus.SUPERSEDED


def test_concurrent_revision_and_cross_team_access_are_rejected():
    first = sample()
    ledger = EventLedger()
    ledger.append(first, expected_previous=0)
    with pytest.raises(RevisionConflict, match="concurrent"):
        ledger.append(first, expected_previous=0)
    with pytest.raises(KeyError):
        ledger.get(uuid.uuid4(), first.event.id)


def test_analysis_must_trace_to_claim_or_evidence():
    first = sample()
    analysis = Analysis(id="a1", section="前因", text="Inference", label="inference")
    with pytest.raises(ValueError, match="fact dependency"):
        validate_revision(first.model_copy(update={"analyses": (analysis,)}))


def test_money_requires_currency_and_period_must_be_valid():
    with pytest.raises(ValueError, match="currency"):
        NumericFact(value=10, kind="money", unit="million")
    with pytest.raises(ValueError, match="period_start"):
        NumericFact(value=10, kind="count", unit="items", period_start=dt.date(2026, 1, 1))
