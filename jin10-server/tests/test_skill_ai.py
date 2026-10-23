"""The five fixed-brief AI polish layer must never invent or drop numbers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.briefs import skill_ai


SOURCE_TITLE = "【A股温度计】两市成交额报1.44万亿元"
SOURCE_BODY = "2026年10月19日，沪深两市成交额为1.44万亿元，较前值减少1179.09亿元，变动-7.58%；连续6个交易日低于20日均线（1.71万亿元）。"


def test_validate_answer_allows_wording_changes_but_not_number_changes():
    title, body = skill_ai.validate_answer(SOURCE_TITLE, SOURCE_BODY, {
        "title": "【A股温度计】成交额回落至1.44万亿元",
        "body": "2026年10月19日，沪深两市成交额1.44万亿元，较前值下降1179.09亿元，降幅7.58%，连续6个交易日低于20日均线（1.71万亿元）。",
    })
    assert "1.44万亿元" in title and "1179.09" in body
    with pytest.raises(ValueError, match="数字"):
        skill_ai.validate_answer(SOURCE_TITLE, SOURCE_BODY, {
            "title": "【A股温度计】成交额报1.45万亿元",
            "body": SOURCE_BODY,
        })
    with pytest.raises(ValueError, match="数字"):
        skill_ai.validate_answer(SOURCE_TITLE, SOURCE_BODY, {
            "title": SOURCE_TITLE, "body": "沪深两市成交额下降。",
        })
    with pytest.raises(ValueError, match="导流"):
        skill_ai.validate_answer(SOURCE_TITLE, SOURCE_BODY, {
            "title": SOURCE_TITLE, "body": SOURCE_BODY + "点击查看完整数据。",
        })


def test_polish_is_disabled_without_a_model(monkeypatch):
    monkeypatch.setattr(skill_ai, "get_settings", lambda: SimpleNamespace(briefs_skill_ai_enabled=True))
    monkeypatch.setattr(skill_ai, "briefs_ai", lambda team: {
        "key": "", "model": "", "base_url": "", "provider": "deepseek"})
    assert skill_ai.polish(None, "volume", SOURCE_TITLE, SOURCE_BODY)["status"] == "not_configured"
    monkeypatch.setattr(skill_ai, "get_settings", lambda: SimpleNamespace(briefs_skill_ai_enabled=False))
    assert skill_ai.polish(None, "volume", SOURCE_TITLE, SOURCE_BODY)["status"] == "disabled"


class _Response:
    def __init__(self, content: dict):
        self.content = json.dumps(content, ensure_ascii=False).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit: int = -1):
        return self.content[:limit] if limit >= 0 else self.content


def test_polish_accepts_a_number_safe_answer(monkeypatch):
    monkeypatch.setattr(skill_ai, "get_settings", lambda: SimpleNamespace(briefs_skill_ai_enabled=True))
    monkeypatch.setattr(skill_ai, "briefs_ai", lambda team: {
        "key": "test-key", "model": "deepseek-chat", "base_url": "https://example.test/v1",
        "provider": "deepseek", "configured": True})
    expected = {
        "title": "【A股温度计】成交额回落至1.44万亿元",
        "body": "2026年10月19日，沪深两市成交额1.44万亿元，较前值减少1179.09亿元，降幅7.58%，连续6个交易日低于20日均线（1.71万亿元）。",
    }
    monkeypatch.setattr(skill_ai, "urlopen", lambda request, timeout: _Response({
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(expected, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }))
    result = skill_ai.polish(None, "volume", SOURCE_TITLE, SOURCE_BODY)
    assert result["status"] == "ok" and result["title"] == expected["title"]
    assert result["usage"]["total_tokens"] == 30


def test_team_skill_disable_blocks_ai_polish(monkeypatch):
    monkeypatch.setattr(skill_ai, "get_settings", lambda: SimpleNamespace(briefs_skill_ai_enabled=True))
    monkeypatch.setattr(skill_ai, "briefs_ai", lambda team: {
        "key": "test-key", "model": "deepseek-chat", "base_url": "https://example.test",
        "provider": "deepseek", "configured": True})
    team = SimpleNamespace(settings={"skills": {"volume": {"enabled": False}}})
    result = skill_ai.polish(team, "volume", SOURCE_TITLE, SOURCE_BODY)
    assert result["status"] == "disabled" and result["reason"] == "skill_disabled"
