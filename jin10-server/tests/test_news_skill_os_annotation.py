"""Plus注解 returns the fixed three-section plain text for one flash."""
from __future__ import annotations

import pytest

from app.config import get_settings
from tests.conftest import login


def enable(monkeypatch):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()


def test_annotation_returns_plain_three_sections(client, monkeypatch):
    enable(monkeypatch)
    body = "前因：\n\n融资计划来自公司公告。\n\n后果：\n\n项目将分阶段推进。\n\n补充说明：\n\n仅据公司公告。"
    monkeypatch.setattr("app.api.news_skill_os.analyze_annotation", lambda team, text, source: {
        "title": "Plus注解｜公司公告", "body": body,
        "sections": {"前因": "融资计划来自公司公告。", "后果": "项目将分阶段推进。", "补充说明": "仅据公司公告。"},
        "sources": [], "warnings": [], "char_count": 30,
        "publication_allowed": False, "human_approval_required": True,
    })
    editor = login(client, "editor@plus.test")
    response = client.post("/api/news-skill-os/annotations/analyze", headers=editor,
                           json={"text": "公司公告显示拟融资1亿美元。"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["body"].startswith("前因：") and "补充说明：" in payload["body"]
    assert payload["publication_allowed"] is False


def test_annotation_is_editor_only(client, monkeypatch):
    enable(monkeypatch)
    viewer = login(client, "viewer@plus.test")
    assert client.post("/api/news-skill-os/annotations/analyze", headers=viewer,
                       json={"text": "一条快讯"}).status_code == 403
