"""No-network contract and three-case workflow tests for the isolated MVP."""

from copy import deepcopy
from importlib.resources import files
from pathlib import Path

import pytest

from app.news_skill_os.manifest import ManifestError, SkillRegistry, bundled_registry
from app.news_skill_os.workflow import Workflow, WorkflowEngine, WorkflowError


MANIFESTS = Path(str(files("app.news_skill_os").joinpath("manifests")))
NUMBER = {"value": 100, "kind": "money", "unit": "million", "currency": "USD"}


def fixture(kind="official", evidence_number=None):
    return {
        "article": {
            "title": "Issuer reports financing", "event_time": "2026-10-20T08:00:00+08:00",
            "sources": [{"id": "s1", "name": "Source", "kind": kind, "original_id": "origin-1"}],
            "assertions": [{"id": "c1", "text": "Issuer announced USD 100 million financing",
                            "subject": "Issuer", "predicate": "financing", "numeric": NUMBER,
                            "source_id": "s1", "evidence_id": "e1", "excerpt": "USD 100 million",
                            "evidence_numeric": NUMBER if evidence_number is None else evidence_number}],
        },
        "background_fixture": {"items": [
            {"section": section, "text": text, "label": "inference",
             "claim_ids": ["c1"], "evidence_ids": ["e1"]}
            for section, text in (("前因", "Financing follows the filed plan"),
                                  ("后果", "Funds may support the announced project"),
                                  ("补充说明", "Terms beyond the filed amount remain unconfirmed"))
        ]},
    }


def setup():
    registry = bundled_registry()
    workflow = Workflow.load_yaml(MANIFESTS / "research-workflow.yaml", registry)
    return registry, workflow, WorkflowEngine(registry)


def test_bundled_manifest_and_workflow_contracts_load():
    registry, workflow, _ = setup()
    assert len(registry.keys()) == 5
    assert workflow.order == ("extraction", "verification", "background", "analysis", "review")


def test_manifest_rejects_permission_escalation_and_unsafe_entrypoint():
    registry = SkillRegistry()
    import yaml
    manifest = yaml.safe_load((MANIFESTS / "event-extraction.yaml").read_text(encoding="utf-8"))
    unsafe = deepcopy(manifest)
    unsafe["permissions"]["publish"] = True
    with pytest.raises(ManifestError, match="permission denied"):
        registry.register(unsafe)
    unsafe = deepcopy(manifest)
    unsafe["entrypoint"] = "os:system"
    with pytest.raises(ManifestError, match="entrypoint"):
        registry.register(unsafe)


def test_manifest_versions_are_immutable_and_schema_is_checked():
    registry = SkillRegistry()
    import yaml
    manifest = yaml.safe_load((MANIFESTS / "event-extraction.yaml").read_text(encoding="utf-8"))
    registry.register(manifest)
    registry.register(manifest)
    changed = deepcopy(manifest)
    changed["description"] = "changed"
    with pytest.raises(ManifestError, match="immutable"):
        registry.register(changed)
    broken = deepcopy(manifest)
    broken["version"] = "0.1.1"
    broken["input_schema"] = {"type": "not-a-json-schema-type"}
    with pytest.raises(ManifestError, match="invalid JSON Schema"):
        registry.register(broken)


def test_official_news_runs_to_reviewed_draft_with_trace():
    _, workflow, engine = setup()
    result = engine.run(workflow, fixture())
    assert result.status == "succeeded"
    assert result.outputs["verification"]["verified_claim_ids"] == ["c1"]
    assert result.outputs["review"]["approved"] is True
    assert set(result.outputs["analysis"]["sections"]) == {"前因", "后果", "补充说明"}
    assert result.outputs["review"]["trace"] == {"claim_ids": ["c1"], "evidence_ids": ["e1"]}
    assert all(len(item.input_digest) == 64 and item.output_digest for item in result.records)


def test_anonymous_rumour_never_becomes_verified_or_approved():
    _, workflow, engine = setup()
    result = engine.run(workflow, fixture(kind="anonymous"))
    assert result.outputs["verification"]["claims"][0]["status"] == "unverified"
    assert result.outputs["analysis"]["draft"]["body"] == ""
    assert result.outputs["review"]["approved"] is False
    assert result.status == "blocked"


def test_erroneous_number_fails_verification_and_review():
    _, workflow, engine = setup()
    wrong = {**NUMBER, "value": 200}
    result = engine.run(workflow, fixture(evidence_number=wrong))
    assert result.outputs["verification"]["claims"][0]["status"] == "rejected"
    assert result.outputs["review"]["approved"] is False
    assert result.status == "blocked"


def test_missing_numeric_evidence_cannot_verify_and_fake_context_is_dropped():
    _, workflow, engine = setup()
    sample = fixture()
    sample["article"]["assertions"][0]["evidence_numeric"] = None
    sample["background_fixture"]["items"][0]["evidence_ids"] = ["fabricated"]
    result = engine.run(workflow, sample)
    assert result.outputs["verification"]["verified_claim_ids"] == []
    assert result.outputs["background"]["context"] == []
    assert result.status == "blocked"


def test_deterministic_cache_and_claim_scoped_invalidation():
    _, workflow, engine = setup()
    first = engine.run(workflow, fixture())
    second = engine.run(workflow, fixture())
    assert first.status == second.status == "succeeded"
    assert all(record.cached for record in second.records)
    assert engine.invalidate_claims({"c1"}) >= 1
    third = engine.run(workflow, fixture())
    assert any(not record.cached for record in third.records)


def test_workflow_rejects_cycles_and_bad_bindings():
    registry, workflow, _ = setup()
    import yaml
    raw = yaml.safe_load((MANIFESTS / "research-workflow.yaml").read_text(encoding="utf-8"))
    cycle = deepcopy(raw)
    cycle["nodes"][0]["depends_on"] = ["review"]
    with pytest.raises(WorkflowError, match="cycle"):
        Workflow.from_dict(cycle, registry)
    bad = deepcopy(raw)
    bad["nodes"][1]["bindings"]["extraction"] = "$node.background"
    with pytest.raises(WorkflowError, match="depend"):
        Workflow.from_dict(bad, registry)


@pytest.mark.parametrize("topic,source_kind,mismatch,approved", [
    ("equity financing", "official", False, True),
    ("bond issuance", "official", False, True),
    ("quarterly revenue", "official", False, True),
    ("annual guidance", "official", False, True),
    ("share buyback", "official", False, True),
    ("central bank repo", "official", False, True),
    ("policy rate", "official", False, True),
    ("employment payrolls", "named_media", False, False),
    ("unemployment rate", "named_media", False, False),
    ("inflation print", "named_media", False, False),
    ("oil shipment", "anonymous", False, False),
    ("refinery outage", "anonymous", False, False),
    ("gas storage", "anonymous", False, False),
    ("AI accelerator", "anonymous", False, False),
    ("chip packaging", "official", True, False),
    ("cloud capex", "official", True, False),
    ("supplier lead time", "official", True, False),
    ("incorrect percentage", "official", True, False),
    ("currency mismatch", "official", True, False),
    ("rumour correction", "named_media", True, False),
])
def test_twenty_offline_topics_respect_evidence_gate(topic, source_kind, mismatch, approved):
    _, workflow, engine = setup()
    sample = fixture(kind=source_kind, evidence_number={**NUMBER, "value": 200} if mismatch else None)
    sample["article"]["title"] = topic
    sample["article"]["assertions"][0]["text"] = f"{topic}: USD 100 million reported"
    result = engine.run(workflow, sample)
    assert set(result.outputs["analysis"]["sections"]) == {"前因", "后果", "补充说明"}
    assert result.outputs["review"]["approved"] is approved
    assert result.status == ("succeeded" if approved else "blocked")
    if not approved:
        assert "c1" not in result.outputs["verification"]["verified_claim_ids"]
