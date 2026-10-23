"""Evidence-bound three-part annotation and draft, with no invented context."""

from __future__ import annotations


SECTIONS = ("前因", "后果", "补充说明")
EMPTY = "暂无可核实的增量信息。"


def run(payload: dict) -> dict:
    verification = payload["verification"]
    background = payload["background"]
    approved = set(verification["verified_claim_ids"])
    context = [item for item in background["context"] if set(item["claim_ids"]) <= approved]
    sections = {section: "\n".join(("已核实：" if item["label"] == "fact" else "推断：") + item["text"]
                                    for item in context if item["section"] == section) or EMPTY
                for section in SECTIONS}
    facts = [claim for claim in verification["claims"] if claim["id"] in approved]
    title = facts[0]["text"] if facts else "待核实新闻草稿"
    body = "\n".join(claim["text"] for claim in facts)
    return {"sections": sections,
            "new_information": [item["text"] for item in context], "analysis_items": context,
            "claim_ids": sorted({ref for item in context for ref in item["claim_ids"]} | {item["id"] for item in facts}),
            "evidence_ids": sorted({ref for item in context for ref in item["evidence_ids"]} |
                                   {ref for item in facts for ref in item["evidence_ids"]}),
            "confidence": "fixture_verified" if facts else "insufficient_evidence",
            "unresolved": [] if facts else ["尚无已核实断言"],
            "draft": {"title": title, "body": body}}

