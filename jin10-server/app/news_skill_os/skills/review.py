"""Draft quality gate. It never publishes or upgrades fact status."""

from __future__ import annotations


def run(payload: dict) -> dict:
    verification = payload["verification"]
    analysis = payload["analysis"]
    verified = set(verification["verified_claim_ids"])
    issues = []
    if not verified:
        issues.append("no verified claim")
    if set(analysis["sections"]) != {"前因", "后果", "补充说明"}:
        issues.append("three-part annotation is incomplete")
    if not set(analysis["claim_ids"]) <= verified:
        issues.append("analysis cites unverified claim")
    evidence_ids = {item["id"] for item in verification["evidence"]}
    if any(item["label"] not in ("fact", "inference") or
           not set(item["claim_ids"]) <= verified or
           not set(item["evidence_ids"]) <= evidence_ids
           for item in analysis["analysis_items"]):
        issues.append("analysis has an unsupported or unlabeled assertion")
    if not analysis["draft"]["body"].strip():
        issues.append("draft has no verified factual body")
    unsupported = [claim["text"] for claim in verification["claims"] if claim["id"] not in verified
                   and claim["text"] in analysis["draft"]["body"]]
    if unsupported:
        issues.append("draft repeats unverified or rejected assertion")
    return {"approved": not issues, "issues": issues,
            "trace": {"claim_ids": sorted(verified & set(analysis["claim_ids"])),
                      "evidence_ids": analysis["evidence_ids"]}}
