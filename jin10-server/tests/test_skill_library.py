"""Team-scoped Skill library: list, versioned overrides and admin-only writes."""
from __future__ import annotations

from app.config import get_settings
from tests.conftest import login


def test_standalone_skill_pages_are_routed(client):
    assert client.get("/skills/").status_code == 200
    assert "SKILL LIBRARY" in client.get("/skills/").text
    assert client.get("/skills/volume/").status_code == 200
    assert client.get("/skills/editorial_quality/").status_code == 200
    assert client.get("/skills/news.event-extraction/").status_code == 404
    assert client.get("/skills/unknown/").status_code == 404


def test_every_member_sees_only_production_text_skills(client):
    for email in ("viewer@plus.test", "editor@plus.test", "admin@plus.test"):
        response = client.get("/api/settings/skills", headers=login(client, email))
        assert response.status_code == 200, response.text
        skills = response.json()["skills"]
        assert [item["category"] for item in skills[:5]] == ["volume", "turnover", "valuation", "liquidity", "oil_etf"]
        assert all(item["enabled"] and not item["customized"] for item in skills[:5])
        assert all(item["instructions"] and item["bundled_instructions"] == item["instructions"] for item in skills[:5])
        assert skills[0]["skill_id"] == "news.fixed-volume"
        assert skills[0]["group"] == "A股温度计" and skills[3]["group"] == "流动性"
        assert len(skills) == 8
        assert all(item["editable"] is True for item in skills)
        assert skills[-1]["category"] == "editorial_quality"
        assert skills[-1]["group"] == "内容质量" and skills[-1]["samples"] == 0
    detail = client.get("/api/settings/skills/volume", headers=login(client, "viewer@plus.test"))
    assert detail.status_code == 200 and detail.json()["skill"]["category"] == "volume"
    assert len(detail.json()["skill"]["history_samples"]) == 20
    quality = client.get("/api/settings/skills/editorial_quality", headers=login(client, "viewer@plus.test"))
    assert quality.status_code == 200 and quality.json()["skill"]["history_samples"] == []
    research = client.get("/api/settings/skills/news.event-extraction", headers=login(client, "viewer@plus.test"))
    assert research.status_code == 404
    assert client.get("/api/settings/skills/unknown", headers=login(client, "viewer@plus.test")).status_code == 404


def test_unused_research_workflow_endpoints_are_removed(client, monkeypatch):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()
    editor = login(client, "editor@plus.test")
    for path in ("/api/news-skill-os/fixtures/official/run",
                 "/api/news-skill-os/workflows/research/runs",
                 "/api/news-skill-os/events/00000000-0000-0000-0000-000000000001/workflows/research/runs"):
        assert client.post(path, headers=editor, json={}).status_code in (404, 405)
    assert client.get("/api/news-skill-os/workflow-runs", headers=editor).status_code == 404
    get_settings.cache_clear()


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


def test_quality_skill_team_override_is_versioned(client):
    admin = login(client, "admin@plus.test")
    response = client.put("/api/settings/skills/editorial_quality", headers=admin, json={
        "revision": 0, "enabled": True, "instructions": "# 团队文字规范\n保留原始来源。"})
    assert response.status_code == 200, response.text
    skill = response.json()["skill"]
    assert skill["revision"] == 1 and skill["customized"] is True
    assert skill["history"][0]["digest"] == skill["digest"]
    detail = client.get("/api/settings/skills/editorial_quality", headers=admin)
    assert detail.json()["skill"]["instructions"] == "# 团队文字规范\n保留原始来源。"


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
