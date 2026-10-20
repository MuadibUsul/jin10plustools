"""Adapt a persisted event revision to the existing research DAG.

The adapter is intentionally conservative: only claims with a supporting
evidence row enter the article, and only analyses with explicit dependencies
become background items.  It never invents facts or upgrades a claim status;
the fact-verification skill re-checks the supplied source/evidence chain.
"""
from __future__ import annotations

from typing import Any, Mapping


def research_input(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    event = snapshot.get("event") or {}
    sources = [dict(item) for item in snapshot.get("sources", [])]
    source_ids = {item.get("id") for item in sources}
    evidence = {item.get("id"): item for item in snapshot.get("evidence", [])}
    support: dict[str, str] = {}
    for link in snapshot.get("links", []):
        if link.get("stance") != "supports":
            continue
        claim_id = link.get("claim_id")
        evidence_id = link.get("evidence_id")
        if claim_id and evidence_id and claim_id not in support:
            support[claim_id] = evidence_id

    assertions = []
    for claim in snapshot.get("claims", []):
        evidence_id = support.get(claim.get("id"))
        evidence_row = evidence.get(evidence_id)
        if evidence_row is None or evidence_row.get("source_id") not in source_ids:
            continue
        assertions.append({
            "id": claim["id"], "text": claim["text"], "subject": claim["subject"],
            "predicate": claim["predicate"], "numeric": claim.get("numeric"),
            "source_id": evidence_row["source_id"], "evidence_id": evidence_row["id"],
            "excerpt": evidence_row["excerpt"],
        })

    items = []
    for analysis in snapshot.get("analyses", []):
        if not analysis.get("claim_ids") and not analysis.get("evidence_ids"):
            continue
        items.append({
            "section": analysis["section"], "text": analysis["text"],
            "label": analysis["label"], "claim_ids": list(analysis.get("claim_ids", [])),
            "evidence_ids": list(analysis.get("evidence_ids", [])),
        })
    return {
        "article": {
            "title": event.get("title", ""), "event_time": event.get("event_time"),
            "sources": sources, "assertions": assertions,
        },
        "background_fixture": {"items": items},
    }
