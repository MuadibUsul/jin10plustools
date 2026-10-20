"""Team-scoped Skill library: list, versioned overrides and admin-only writes."""
from __future__ import annotations

from tests.conftest import login


def test_standalone_skill_pages_are_routed(client):
    assert client.get("/skills/").status_code == 200
    assert "SKILL LIBRARY" in client.get("/skills/").text
    assert client.get("/skills/volume/").status_code == 200
    assert client.get("/skills/news.event-extraction/").status_code == 200
    assert client.get("/skills/unknown/").status_code == 404


def test_every_member_can_read_five_installed_skills(client):
    for email in ("viewer@plus.test", "editor@plus.test", "admin@plus.test"):
        response = client.get("/api/settings/skills", headers=login(client, email))
        assert response.status_code == 200, response.text
        skills = response.json()["skills"]
        assert [item["category"] for item in skills[:5]] == ["volume", "turnover", "valuation", "liquidity", "oil_etf"]
        assert all(item["enabled"] and not item["customized"] for item in skills[:5])
        assert all(item["instructions"] and item["bundled_instructions"] == item["instructions"] for item in skills[:5])
        assert skills[0]["skill_id"] == "news.fixed-volume"
        assert skills[0]["group"] == "A股温度计" and skills[3]["group"] == "流动性"
        research = [item for item in skills if item.get("group") == "研究型 Skill"]
        assert len(research) == 5 and all(item["editable"] is False for item in research)
        assert {item["skill_id"] for item in research} == {
            "news.event-extraction", "news.fact-verification", "news.background-research",
            "finance.causal-analysis", "news.quality-review"}
    detail = client.get("/api/settings/skills/volume", headers=login(client, "viewer@plus.test"))
    assert detail.status_code == 200 and detail.json()["skill"]["category"] == "volume"
    research = client.get("/api/settings/skills/news.event-extraction", headers=login(client, "viewer@plus.test"))
    assert research.status_code == 200 and research.json()["skill"]["editable"] is False
    assert client.get("/api/settings/skills/unknown", headers=login(client, "viewer@plus.test")).status_code == 404


def test_admin_override_is_versioned_and_can_be_restored(client):
    admin = login(client, "admin@plus.test")
    first = client.put("/api/settings/skills/volume", headers=admin, json={
        "revision": 0, "enabled": True, "instructions": "# 团队成交额规范\n只写已核对事实。"})
    assert first.status_code == 200, first.text
    skill = first.json()["skill"]
    assert skill["customized"] is True and skill["revision"] == 1
    assert skill["instructions"].startswith("# 团队成交额规范")
    assert skill["history"][-1]["revision"] == 1

    conflict = client.put("/api/settings/skills/volume", headers=admin, json={
        "revision": 0, "enabled": False, "instructions": "旧版本"})
    assert conflict.status_code == 409

    disabled = client.put("/api/settings/skills/volume", headers=admin, json={
        "revision": 1, "enabled": False, "instructions": "# 停用但仍保留规范"})
    assert disabled.status_code == 200
    assert disabled.json()["skill"]["enabled"] is False and disabled.json()["skill"]["revision"] == 2

    restored = client.put("/api/settings/skills/volume", headers=admin, json={
        "revision": 2, "enabled": True, "instructions": ""})
    assert restored.status_code == 200
    skill = restored.json()["skill"]
    assert skill["customized"] is False and skill["instructions"] == skill["bundled_instructions"]
    assert skill["history"][-1]["customized"] is False


def test_editor_cannot_write_and_other_team_does_not_see_override(client):
    editor = login(client, "editor@plus.test")
    admin = login(client, "admin@plus.test")
    outsider = login(client, "out@plus.test")
    assert client.put("/api/settings/skills/volume", headers=editor, json={
        "revision": 0, "enabled": True, "instructions": "editor change"}).status_code == 403
    saved = client.put("/api/settings/skills/turnover", headers=admin, json={
        "revision": 0, "enabled": True, "instructions": "team turnover rules"})
    assert saved.status_code == 200
    own = client.get("/api/settings/skills", headers=admin).json()["skills"]
    other = client.get("/api/settings/skills", headers=outsider).json()["skills"]
    assert own[1]["customized"] is True
    assert other[1]["customized"] is False and other[1]["instructions"] != "team turnover rules"
    assert client.put("/api/settings/skills/not-a-skill", headers=admin, json={
        "revision": 0, "enabled": True, "instructions": "x"}).status_code == 404
