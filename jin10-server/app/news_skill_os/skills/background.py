"""Read only locally supplied background items tied to verified claims."""

from __future__ import annotations


def run(payload: dict) -> dict:
    verified = set(payload["verification"]["verified_claim_ids"])
    evidence = {item["id"] for item in payload["verification"]["evidence"]}
    context = [item for item in payload["background_fixture"].get("items", [])
               if set(item.get("claim_ids", [])) and set(item.get("claim_ids", [])) <= verified
               and set(item.get("evidence_ids", [])) and set(item.get("evidence_ids", [])) <= evidence
               and item.get("section") in ("前因", "后果", "补充说明") and item.get("text")
               and item.get("label") in ("fact", "inference")]
    return {"context": context,
            "claim_ids": sorted({ref for item in context for ref in item["claim_ids"]}),
            "evidence_ids": sorted({ref for item in context for ref in item["evidence_ids"]})}

