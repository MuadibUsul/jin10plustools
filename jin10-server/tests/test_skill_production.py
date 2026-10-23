"""Fixed briefs use Skill copy in production, with a complete fixed fallback."""
from __future__ import annotations

from app.briefs import skill_production


def payload():
    return {"title": "规则标题", "dynamic_body": "规则正文", "warnings": []}


def test_successful_skill_copy_replaces_wording(monkeypatch):
    monkeypatch.setattr(skill_production, "polish", lambda team, category, title, body: {
        "status": "ok", "provider": "deepseek", "model": "deepseek-chat",
        "title": "Skill标题", "body": "Skill正文"})
    result = skill_production.apply_skill_copy(payload(), "volume", {})
    assert result["title"] == "Skill标题" and result["dynamic_body"] == "Skill正文"
    assert result["skill"]["status"] == "ok" and not result["warnings"]


def test_quota_failure_keeps_fixed_combination(monkeypatch):
    monkeypatch.setattr(skill_production, "polish", lambda *args: {
        "status": "failed", "reason": "provider_http_error", "http_status": 429})
    result = skill_production.apply_skill_copy(payload(), "turnover", {})
    assert result["title"] == "规则标题" and result["dynamic_body"] == "规则正文"
    assert any("额度不足" in warning for warning in result["warnings"])


def test_other_categories_or_errors_never_lose_the_rule_copy(monkeypatch):
    original = payload()
    assert skill_production.apply_skill_copy(original, "us_structure", {}) is original
    monkeypatch.setattr(skill_production, "polish", lambda *args: {
        "status": "failed", "reason": "skill_disabled"})
    result = skill_production.apply_skill_copy(payload(), "valuation", {})
    assert result["title"] == "规则标题" and result["dynamic_body"] == "规则正文"
    assert any("自动回退固定组合" in warning for warning in result["warnings"])
