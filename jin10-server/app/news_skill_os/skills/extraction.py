"""Extract pre-annotated assertions from an offline article fixture.

This deterministic MVP does not pretend to parse arbitrary prose. A later
model-backed adapter may propose assertions, which must still be verified.
"""

from __future__ import annotations

import hashlib
import json


def run(payload: dict) -> dict:
    article = payload["article"]
    stable = json.dumps(article, sort_keys=True, ensure_ascii=False)
    event_id = hashlib.sha256(stable.encode()).hexdigest()[:24]
    sources = article.get("sources", [])
    claims = []
    evidence = []
    for assertion in article.get("assertions", []):
        claims.append({key: assertion[key] for key in ("id", "text", "subject", "predicate")})
        claims[-1]["numeric"] = assertion.get("numeric")
        claims[-1]["source_id"] = assertion["source_id"]
        citations = assertion.get("evidence") or [{"id": assertion["evidence_id"],
            "source_id": assertion["source_id"], "excerpt": assertion["excerpt"],
            "numeric": assertion.get("evidence_numeric")}]
        evidence.extend({"id": item["id"], "claim_id": assertion["id"],
                         "source_id": item["source_id"], "excerpt": item["excerpt"],
                         "numeric": item.get("numeric")} for item in citations)
    return {"event": {"id": event_id, "title": article["title"], "time": article["event_time"]},
            "sources": sources, "claims": claims, "evidence": evidence}

