"""Independent GPU editorial pilot checks; synthetic fixtures are not market data.

These assertions measure factual bindings and safe integration. They deliberately
do not label prose as editor-approved or use whole paragraphs as a style oracle.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from app.briefs import gpu_editorial


FIXTURE = Path(__file__).parent / "fixtures" / "gpu_editorial_cases.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = DATA["cases"]
FORBIDDEN_JUDGMENTS = (
    "创半年新高", "历史新高", "结构性紧缺", "全面紧缺", "需求迁移",
    "供应紧缺", "需求旺盛", "投资建议", "点击查看", "注：",
)


def baseline_payload():
    return deepcopy(CASES[0]["payload"])


def without_editorial(payload):
    return {key: value for key, value in payload.items() if key != "editorial"}


def test_fixture_is_explicitly_synthetic_and_keeps_holdout_outside_production():
    assert DATA["schema_version"] == 1
    assert DATA["provenance"] == "synthetic_not_market_data"
    assert len(CASES) >= 30
    assert len({case["id"] for case in CASES}) == len(CASES)
    assert {case["split"] for case in CASES} == {"development", "holdout"}
    assert all(case["expect"]["status"] in {"ok", "rejected"} for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_editorial_fact_and_boundary_cases(case):
    payload = deepcopy(case["payload"])
    original = deepcopy(payload)
    expect = case["expect"]
    if expect["status"] == "rejected":
        with pytest.raises(ValueError, match=expect["reason"]):
            gpu_editorial.build_candidate(payload)
        assert payload == original
        return

    candidate = gpu_editorial.build_candidate(payload)
    assert payload == original, "candidate generation must not mutate the validated payload"
    assert candidate["status"] == "candidate"
    assert candidate["hard_checks"]["facts"] is True
    assert candidate["hard_checks"]["structure"] is True
    assert candidate["title"].startswith("【AI景气度指标】")
    assert "\n" not in candidate["title"]
    assert "\n" not in candidate["body"]
    assert candidate["scenario"]
    assert set(candidate["plan"]) == {"headline", "lead", "comparison", "closing"}
    assert all(isinstance(value, str) and value for value in candidate["plan"].values())
    assert candidate["pack_version"] and candidate["pack_digest"]
    assert candidate["facts_digest"]
    assert isinstance(candidate["example_ids"], list)
    text = candidate["title"] + "\n" + candidate["body"]
    for phrase in FORBIDDEN_JUDGMENTS + tuple(expect.get("forbidden", [])):
        assert phrase not in text
    for phrase in expect.get("title_forbidden", []):
        assert phrase not in candidate["title"]
    for pattern in expect.get("title_patterns", []):
        assert re.search(pattern, candidate["title"]), (pattern, candidate["title"])
    for pattern in expect.get("body_patterns", []):
        assert re.search(pattern, candidate["body"]), (pattern, candidate["body"])

    # Prices remain attached to their named card, including display precision.
    for metric in ("b200", "h100", "a100"):
        price = payload["facts"][metric]
        assert re.search(metric.upper() + r"[^。；;]*?" + re.escape(price), candidate["body"])
        change = payload["facts"].get(metric + "_change_pct")
        if change not in (None, "") and Decimal(change) != 0:
            printed = str(change).lstrip("+-") + "%"
            assert printed in candidate["body"], (metric, printed, candidate["body"])
    assert "美元/GPU每小时" in candidate["body"]
    assert payload["fixed_notes"] not in candidate["body"]


def test_shadow_preserves_the_entire_existing_product():
    payload = baseline_payload()
    payload["rule_copy"] = {"title": payload["title"], "body": payload["dynamic_body"]}
    payload["image_sha256"] = {"existing-gpu.png": "unchanged-image-digest"}
    original = deepcopy(payload)
    result = gpu_editorial.apply_gpu_editorial(payload, {}, mode="shadow")
    assert result is payload
    assert without_editorial(result) == original
    assert result["editorial"]["status"] == "shadow"
    assert result["editorial"]["baseline"] == {
        "title": original["title"], "body": original["dynamic_body"],
    }
    assert result["editorial"]["candidate"]["status"] == "candidate"


def test_default_is_shadow_and_off_is_a_noop(monkeypatch):
    monkeypatch.setattr(gpu_editorial, "get_settings", lambda: SimpleNamespace(
        briefs_gpu_editorial_mode="shadow"))
    payload = baseline_payload()
    gpu_editorial.apply_gpu_editorial(payload, {})
    assert payload["editorial"]["mode"] == "shadow"
    off = baseline_payload()
    original = deepcopy(off)
    assert gpu_editorial.apply_gpu_editorial(off, {}, mode="off") is off
    assert off == original and "editorial" not in off


def test_draft_pack_cannot_replace_the_live_copy_even_in_active_mode():
    payload = baseline_payload()
    original = deepcopy(payload)
    gpu_editorial.apply_gpu_editorial(payload, {}, mode="active")
    assert without_editorial(payload) == original
    assert payload["editorial"]["status"] == "shadow"
    assert payload["editorial"]["reason"] == "pack_not_reviewed"


@pytest.mark.parametrize("override", [
    {"enabled": False},
    {"instructions": "团队已有独立审核的规范，应保留该覆盖。"},
])
def test_team_opt_out_and_custom_instructions_are_respected(override):
    payload = baseline_payload()
    original = deepcopy(payload)
    gpu_editorial.apply_gpu_editorial(payload, {"skills": {"gpu_rental": override}}, mode="shadow")
    assert without_editorial(payload) == original
    assert payload["editorial"]["status"] == "skipped"
    assert payload["editorial"]["reason"]


def test_custom_body_template_is_preserved():
    payload = baseline_payload()
    payload["body_template_custom"] = True
    original = deepcopy(payload)
    gpu_editorial.apply_gpu_editorial(payload, {}, mode="shadow")
    assert without_editorial(payload) == original
    assert payload["editorial"]["status"] == "skipped"


def test_bad_facts_fall_back_without_changing_delivery():
    payload = baseline_payload()
    payload["facts"]["h100"] = "NaN"
    original = deepcopy(payload)
    gpu_editorial.apply_gpu_editorial(payload, {}, mode="shadow")
    assert without_editorial(payload) == original
    assert payload["editorial"]["status"] == "fallback"


def test_missing_style_asset_cannot_fail_an_existing_product(monkeypatch):
    def missing_pack():
        raise OSError("missing style asset")
    monkeypatch.setattr(gpu_editorial, "load_pack", missing_pack)
    payload = baseline_payload()
    original = deepcopy(payload)
    gpu_editorial.apply_gpu_editorial(payload, {}, mode="shadow")
    assert without_editorial(payload) == original
    assert payload["editorial"]["status"] == "fallback"


def test_invalid_module_selection_is_rejected_instead_of_joining_arbitrary_text():
    payload = baseline_payload()
    candidate = gpu_editorial.build_candidate(payload)
    invalid = {**candidate["plan"], "lead": "任意写入的事实段"}
    with pytest.raises(ValueError, match="invalid_plan"):
        gpu_editorial.build_candidate(payload, selection=invalid)
    with pytest.raises(ValueError, match="invalid_plan"):
        gpu_editorial.build_candidate(payload, selection={**candidate["plan"], "extra": "x"})


def test_repeated_input_is_reproducible_and_a_revision_changes_fact_identity():
    original = baseline_payload()
    first = gpu_editorial.build_candidate(original)
    assert gpu_editorial.build_candidate(deepcopy(original)) == first
    revised = deepcopy(original)
    revised["facts"].update({"a100": "1.49", "observation_revision": 2})
    next_candidate = gpu_editorial.build_candidate(revised)
    assert next_candidate["facts_digest"] != first["facts_digest"]
    assert "1.49" in next_candidate["body"]
    assert next_candidate["pack_digest"] == first["pack_digest"]


def test_integration_only_adds_gpu_shadow_metadata(monkeypatch):
    from app.briefs import skill_production
    monkeypatch.setattr(skill_production, "polish", lambda *args: {
        "status": "not_configured", "reason": "provider_not_configured"})
    monkeypatch.setattr(gpu_editorial, "get_settings", lambda: SimpleNamespace(
        briefs_gpu_editorial_mode="shadow"))
    gpu = baseline_payload()
    skill_production.apply_skill_copy(gpu, "gpu_rental", {})
    assert gpu["editorial"]["status"] == "shadow"
    assert gpu["editorial"]["baseline"] == {
        "title": gpu["title"], "body": gpu["dynamic_body"],
    }
    other = baseline_payload()
    original_copy = (other['title'], other['dynamic_body'])
    skill_production.apply_skill_copy(other, "volume", {})
    # The suite now covers volume too, but cannot treat GPU facts as volume data.
    assert other['editorial']['status'] == 'fallback'
    assert (other['title'], other['dynamic_body']) == original_copy
