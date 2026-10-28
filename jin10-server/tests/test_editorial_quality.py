"""The shared quality Skill affects both news drafts without changing facts."""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from app.news_skill_os import annotation_ai
from app.news_skill_os.editorial_quality import prompt_for, review_text


def test_quality_skill_override_and_advisory_review():
    team = SimpleNamespace(settings={"skills": {"editorial_quality": {
        "enabled": True, "revision": 1, "instructions": "# 团队规范\n保留来源和限定条件。",
    }}})
    assert "保留来源和限定条件" in prompt_for(team)
    result = review_text(team, target="fresh", title="融资计划", body="总体来看，公司计划融资。",
                         notes="字数：12字", unverified=["融资是否获批"])
    assert result["status"] == "review"
    assert len(result["issues"]) == 2
    team.settings["skills"]["editorial_quality"]["enabled"] = False
    assert prompt_for(team) == ""
    assert review_text(team, target="fresh", title="融资计划", body="总体来看，公司计划融资。") == {
        "skill_id": "news.editorial-quality", "digest": result["digest"],
        "status": "disabled", "issues": [],
    }


def test_annotation_rewrites_vague_prose_before_delivery(monkeypatch):
    team = SimpleNamespace(settings={"skills": {"editorial_quality": {
        "enabled": True, "instructions": "# 团队规范\n只写可核对的条件。",
    }}})
    monkeypatch.setattr(annotation_ai, "briefs_ai", lambda _team: {
        "key": "test", "model": "test-model", "base_url": "https://ai.example.test",
    })
    monkeypatch.setattr(annotation_ai, "_search_context", lambda _text: ([], []))
    calls = []
    replies = iter([
        "前因：\n\n总体来看，公司宣布融资。\n\n后果：\n\n资金拟用于扩产。\n\n补充说明：\n\n项目仍待审批。",
        "前因：\n\n公司宣布融资。\n\n后果：\n\n资金拟用于扩产。\n\n补充说明：\n\n项目仍待审批。",
    ])

    def fake_urlopen(request, timeout):
        calls.append(json.loads(request.data))
        answer = next(replies)
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": answer,
        }}]}).encode())

    monkeypatch.setattr(annotation_ai, "urlopen", fake_urlopen)
    result = annotation_ai.analyze(team, "公司宣布融资。")
    assert len(calls) == 2
    assert "只写可核对的条件" in calls[0]["messages"][0]["content"]
    assert result["quality_review"]["status"] == "no_flags"
    assert "总体来看" not in result["body"]
    assert result["sections"]["后果"] == "资金拟用于扩产。"


def test_annotation_does_not_deliver_a_still_vague_rewrite(monkeypatch):
    team = SimpleNamespace(settings={})
    monkeypatch.setattr(annotation_ai, "briefs_ai", lambda _team: {
        "key": "test", "model": "test-model", "base_url": "https://ai.example.test",
    })
    monkeypatch.setattr(annotation_ai, "_search_context", lambda _text: ([], []))
    answer = "前因：\n\n总体来看，公司宣布融资。\n\n后果：\n\n资金拟用于扩产。\n\n补充说明：\n\n项目仍待审批。"
    monkeypatch.setattr(annotation_ai, "urlopen", lambda request, timeout: io.BytesIO(
        json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": answer,
        }}]}).encode()))
    with pytest.raises(annotation_ai.AnnotationAIError, match="自动改写未通过"):
        annotation_ai.analyze(team, "公司宣布融资。")
