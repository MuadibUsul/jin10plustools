"""Offline hard-check replay and anonymous A/B review for the GPU copy pilot.

This script never invokes an AI provider, opens a database, or modifies source
assets. Synthetic cases exercise safety and coverage; they cannot establish an
editor's acceptance rate or represent live market observations.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re
import sys


SERVER = Path(__file__).resolve().parents[1]
REPOSITORY = SERVER.parent
DEFAULT_CASES = SERVER / "tests" / "fixtures" / "gpu_editorial_cases.json"


def read_cases(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 1 or not isinstance(data.get("cases"), list):
        raise ValueError("Expected a schema_version=1 case collection")
    ids = set()
    for case in data["cases"]:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or case["id"] in ids or not isinstance(case.get("payload"), dict)
                or case.get("split") not in {"development", "holdout"}
                or case.get("expect", {}).get("status") not in {"ok", "rejected"}):
            raise ValueError("Each case needs a unique id, split, payload, and expected status")
        ids.add(case["id"])
    return data


def check_expectations(case: dict, candidate: dict | None, error: str | None) -> list[str]:
    """Check the independent case assertions, not an aesthetic model score."""
    expected = case["expect"]
    accepted = candidate is not None and candidate.get("status") == "candidate"
    failures = []
    if expected["status"] == "rejected":
        if accepted:
            failures.append("unsafe_acceptance: case expected rejection")
        elif expected.get("reason") and expected["reason"] not in (error or ""):
            failures.append("rejection_reason_mismatch")
        return failures
    if not accepted:
        return ["unexpected_rejection: " + (error or "candidate not returned")]
    hard_checks = candidate.get("hard_checks")
    if not isinstance(hard_checks, dict) or hard_checks.get("facts") is not True:
        failures.append("candidate_fact_check_not_passed")
    if not isinstance(hard_checks, dict) or hard_checks.get("structure") is not True:
        failures.append("candidate_structure_check_not_passed")
    title, body = str(candidate.get("title", "")), str(candidate.get("body", ""))
    for field, text in (("title_patterns", title), ("body_patterns", body)):
        for pattern in expected.get(field, []):
            if re.search(pattern, text) is None:
                failures.append(field + ": " + pattern)
    for phrase in expected.get("forbidden", []):
        if phrase in title + body:
            failures.append("forbidden: " + phrase)
    for phrase in expected.get("title_forbidden", []):
        if phrase in title:
            failures.append("title_forbidden: " + phrase)
    if expected.get("partial_weekly") is True and "基准缺失" not in body:
        failures.append("missing_weekly_baseline_not_disclosed")
    return failures


def render_markdown(review_rows: list[dict], provenance: str) -> str:
    lines = [
        "# GPU 快讯匿名对照稿", "",
        "资料性质：" + provenance, "",
        "本文件仅供人工盲评。A/B 已随机换位；它们不是优劣标签。",
        ("合成场景不是实际行情。" if provenance == "synthetic_not_market_data" else
         "本文件使用存档事实包，不代表已重新核验当前行情。")
        + "程序硬检查通过不代表编辑风格通过。",
        "请分别记录：可直接采用／仅需微调／需要实质改写，以及具体修改原因。", "",
    ]
    for row in review_rows:
        lines.extend(["## " + row["review_id"], ""])
        for label in ("A", "B"):
            copy = row[label]
            lines.extend(["### " + label, "", copy["title"], "", copy["body"], ""])
        lines.extend([
            "选择：____　A 可用程度：____　B 可用程度：____", "",
            "修改原因：________________________________________________", "",
        ])
    return "\n".join(lines)


def evaluate(data: dict, seed: str) -> tuple[dict, list[dict], list[dict]]:
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    from app.briefs.ai_compute_style import apply_compute_style
    from app.briefs.gpu_editorial import build_candidate, load_pack

    pack = load_pack()
    results, comparisons = [], []
    for case in sorted(data["cases"], key=lambda item: item["id"]):
        baseline_payload = deepcopy(case["payload"])
        baseline_error = None
        try:
            apply_compute_style(baseline_payload, "gpu_rental")
        except (ValueError, TypeError, ArithmeticError, KeyError) as exc:
            baseline_error = str(exc)
        baseline = {"title": str(baseline_payload.get("title", "")),
                    "body": str(baseline_payload.get("dynamic_body", ""))}
        candidate, error = None, None
        try:
            candidate = build_candidate(deepcopy(case["payload"]), pack=pack)
            if not isinstance(candidate, dict):
                error, candidate = "candidate result is not a dict", None
            elif candidate.get("status") != "candidate":
                error = str(candidate.get("reason") or candidate.get("status") or "missing status")
        except (ValueError, TypeError, ArithmeticError, KeyError) as exc:
            error = str(exc)
        failures = check_expectations(case, candidate, error)
        accepted = candidate is not None and candidate.get("status") == "candidate"
        results.append({
            "case_id": case["id"], "split": case["split"],
            "expected_status": case["expect"]["status"],
            "actual_status": "candidate" if accepted else "rejected",
            "rejection_reason": error, "assertion_failures": failures,
            "hard_checks": candidate.get("hard_checks") if candidate else None,
            "scenario": candidate.get("scenario") if candidate else None,
            "plan": candidate.get("plan") if candidate else None,
            "pack_version": candidate.get("pack_version") if candidate else None,
            "pack_digest": candidate.get("pack_digest") if candidate else None,
            "facts_digest": candidate.get("facts_digest") if candidate else None,
            "example_ids": candidate.get("example_ids") if candidate else None,
            "baseline_error": baseline_error,
        })
        if accepted and not baseline_error:
            order = ["baseline", "candidate"]
            random.Random(seed + ":" + case["id"]).shuffle(order)
            copies = {"baseline": baseline,
                      "candidate": {"title": candidate["title"], "body": candidate["body"]}}
            comparisons.append({"case_id": case["id"], "split": case["split"],
                                "A": copies[order[0]], "B": copies[order[1]],
                                "mapping": {"A": order[0], "B": order[1]}})
    random.Random(seed).shuffle(comparisons)
    review, key = [], []
    for index, comparison in enumerate(comparisons, start=1):
        review_id = f"样稿{index:03d}"
        review.append({"review_id": review_id, "A": comparison["A"], "B": comparison["B"],
                       "human_review": {"preferred": None, "A_readiness": None,
                                        "B_readiness": None, "reason": None}})
        key.append({"review_id": review_id, "case_id": comparison["case_id"],
                    "split": comparison["split"], "mapping": comparison["mapping"]})
    expected_rejections = sum(item["expected_status"] == "rejected" for item in results)
    summary = {
        "case_count": len(results), "review_pair_count": len(review),
        "development_count": sum(item["split"] == "development" for item in results),
        "holdout_count": sum(item["split"] == "holdout" for item in results),
        "expected_rejection_count": expected_rejections,
        "actual_rejection_count": sum(item["actual_status"] == "rejected" for item in results),
        "unexpected_rejection_count": sum(item["expected_status"] == "ok" and
                                            item["actual_status"] == "rejected" for item in results),
        "unsafe_acceptance_count": sum(item["expected_status"] == "rejected" and
                                        item["actual_status"] == "candidate" for item in results),
        "assertion_failure_case_count": sum(bool(item["assertion_failures"]) for item in results),
        "human_direct_acceptance_rate": None,
        "style_accepted": None,
    }
    report = {
        "schema_version": 1, "provenance": data.get("provenance", "unspecified"),
        "seed": seed, "model_calls": 0, "database_access": False,
        "pack_version": pack.get("version"), "pack_digest": pack.get("digest"),
        "pack_status": pack.get("status"), "summary": summary, "cases": results,
        "limitations": [("Synthetic cases are not live market data." if
                          data.get("provenance") == "synthetic_not_market_data" else
                          "Archived observations have not been reverified against live sources."),
                        "Passing hard checks does not establish editorial quality.",
                        "Human acceptance is intentionally ungraded until blind review."],
    }
    return report, review, key


def incorporate_reviews(path: Path, report: dict, review: list[dict], key: list[dict]) -> None:
    """Score only matching, explicitly completed human reviews; never approve a pack."""
    submitted = json.loads(path.read_text(encoding="utf-8-sig"))
    if submitted.get("pack_digest") != report["pack_digest"]:
        raise ValueError("Review pack digest does not match the evaluated version")
    rows = submitted.get("cases")
    if not isinstance(rows, list) or len(rows) != len(review):
        raise ValueError("Review case collection does not match")
    by_id = {row["review_id"]: row for row in review}
    mapping = {row["review_id"]: row["mapping"] for row in key}
    seen, counts = set(), {"baseline": {}, "candidate": {}}
    feedback = []
    for row in rows:
        review_id = row.get("review_id")
        if review_id not in by_id or review_id in seen:
            raise ValueError("Unknown or duplicate review id")
        seen.add(review_id)
        if any(row.get(label) != by_id[review_id][label] for label in ("A", "B")):
            raise ValueError("Reviewed prose has changed; rerun blind review")
        human = row.get("human_review") or {}
        if human.get("preferred") not in (None, "A", "B", "tie"):
            raise ValueError("Preferred must be A, B, tie or null")
        for label in ("A", "B"):
            readiness = human.get(label + "_readiness")
            if readiness not in (None, "direct", "minor", "rewrite"):
                raise ValueError("Readiness must be direct, minor, rewrite or null")
            if readiness is not None:
                bucket = counts[mapping[review_id][label]]
                bucket[readiness] = bucket.get(readiness, 0) + 1
        feedback.append({"review_id": review_id, "human_review": human})
    summary = report["summary"]
    summary["human_review_counts"] = counts
    total = sum(counts["candidate"].values())
    summary["human_candidate_rated_count"] = total
    summary["human_direct_acceptance_rate"] = counts["candidate"].get("direct", 0) / total if total else None
    # Human acceptance and sample coverage still require an editorial release decision.
    report["human_feedback"] = feedback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, required=True,
                        help="New output directory; existing directories are never overwritten")
    parser.add_argument("--seed", default="gpu-editorial-20261029")
    parser.add_argument("--reviews", type=Path,
                        help="Completed blind-review.json from this exact pack and prose")
    args = parser.parse_args()
    out = args.out.resolve()
    protected = (SERVER / "app", SERVER / "tests", REPOSITORY / "skills", REPOSITORY / "deploy")
    if out in (SERVER, REPOSITORY) or any(out.is_relative_to(path.resolve()) for path in protected):
        parser.error("Output must be outside production source, tests, skills, and deploy directories")
    if out.exists():
        parser.error("Output directory already exists; choose a new directory")
    data = read_cases(args.cases)
    report, review, key = evaluate(data, args.seed)
    if args.reviews:
        incorporate_reviews(args.reviews, report, review, key)
    report["case_file_sha256"] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=False)
    outputs = {
        "evaluation.json": report,
        "blind-review.json": {"schema_version": 1, "provenance": report["provenance"],
                              "pack_digest": report["pack_digest"], "cases": review},
        "review-key.json": {"schema_version": 1, "seed": args.seed, "cases": key},
    }
    for name, content in outputs.items():
        with (out / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
    with (out / "blind-review.md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(render_markdown(review, report["provenance"]))
    print(json.dumps({"out": str(out), **report["summary"]}, ensure_ascii=False))
    return 1 if report["summary"]["assertion_failure_case_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
