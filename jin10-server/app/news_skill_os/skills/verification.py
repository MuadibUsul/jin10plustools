"""Conservative fixture-based fact checks with original-source deduplication."""

from __future__ import annotations


def run(payload: dict) -> dict:
    extraction = payload["extraction"]
    sources = {source["id"]: source for source in extraction["sources"]}
    evidence = extraction["evidence"]
    checked = []
    warnings = []
    verified_ids = []
    for claim in extraction["claims"]:
        attached = [item for item in evidence if item["claim_id"] == claim["id"]]
        roots = {sources[item["source_id"]]["original_id"] for item in attached}
        numeric = claim.get("numeric")
        cited_numbers = [item.get("numeric") for item in attached if item.get("numeric") is not None]
        mismatch = numeric is not None and any(value != numeric for value in cited_numbers)
        missing_number = numeric is not None and not cited_numbers
        status = ("rejected" if mismatch else
                  "verified" if any(sources[item["source_id"]]["kind"] == "official" for item in attached)
                  or len(roots) >= 2 and not missing_number else
                  "unverified")
        if missing_number and status == "verified":
            status = "unverified"
        checked.append({**claim, "status": status,
                        "evidence_ids": [item["id"] for item in attached]})
        if status == "verified":
            verified_ids.append(claim["id"])
        elif status == "rejected":
            warnings.append(f"{claim['id']}: asserted number conflicts with cited evidence")
        elif missing_number:
            warnings.append(f"{claim['id']}: cited evidence does not contain the asserted number")
        else:
            warnings.append(f"{claim['id']}: no independent verified source")
    return {"claims": checked, "evidence": evidence, "verified_claim_ids": verified_ids, "warnings": warnings}

