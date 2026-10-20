"""A public link becomes an analysis report and a reviewable fresh draft."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import NewsDraft
from tests.conftest import login


def enable(monkeypatch):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()


def test_link_analysis_and_fresh_draft(client, db_factory, monkeypatch):
    enable(monkeypatch)
    article = {"url": "https://example.test/story", "title": "某公司公告",
               "site": "示例新闻", "published_at": "2026-10-20T08:00:00+08:00",
               "text": "公司公告显示，拟融资1亿美元，用于项目建设。管理层表示项目将分阶段推进。"}
    analysis = {
        "timeline": ["2026年10月20日：公司公告融资计划"],
        "key_numbers": ["1亿美元"],
        "source_quotes": ["拟融资1亿美元"],
        "judgment": ["项目将分阶段推进"],
        "unverified": [],
        "warnings": ["仅单一来源"],
        "dateline": "【北京】", "title": "某公司公告拟融资1亿美元",
        "body": "【北京】某公司公告显示，拟融资1亿美元，用于项目建设。管理层表示项目将分阶段推进。",
        "notes": "字数：40字；未经核实：融资进度；仅据公司公告。",
    }
    monkeypatch.setattr("app.api.news_skill_os.fetch_article", lambda url: article)
    monkeypatch.setattr("app.api.news_skill_os.analyze_fresh_link", lambda team, item, focus: {
        "event_title": "某公司公告融资计划",
        "claims": [{"id": "c1", "subject": "某公司", "predicate": "融资金额",
                    "text": "拟融资1亿美元", "evidence": "拟融资1亿美元"}],
        "analysis": analysis, "sources": ["https://example.test/story"],
    })
    editor = login(client, "editor@plus.test")
    response = client.post("/api/news-skill-os/from-link/analyze", headers=editor,
                           json={"url": "https://example.test/story", "focus": ""})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["claim_count"] == 1 and payload["analysis"]["title"] == analysis["title"]
    event_id = payload["event"]["id"]
    detail = client.get(f"/api/news-skill-os/events/{event_id}", headers=editor).json()
    assert detail["event"]["payload"]["notes"] == analysis["notes"]
    draft = client.post(f"/api/news-skill-os/events/{event_id}/fresh-draft", headers=editor)
    assert draft.status_code == 201, draft.text
    assert draft.json()["target"] == "fresh" and analysis["notes"] in draft.json()["body"]
    with db_factory() as db:
        assert len(db.scalars(select(NewsDraft)).all()) == 1


def test_link_permissions(client, monkeypatch):
    enable(monkeypatch)
    viewer = login(client, "viewer@plus.test")
    assert client.post("/api/news-skill-os/from-link/analyze", headers=viewer,
                       json={"url": "https://example.test/story"}).status_code == 403
