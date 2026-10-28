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


def test_external_quotes_are_saved_in_the_event_fact_chain(client, monkeypatch):
    enable(monkeypatch)
    article = {"url": "https://source.example/story", "title": "融资公告",
               "site": "源头", "published_at": "", "text": "公司计划融资1亿美元。"}
    monkeypatch.setattr("app.api.news_skill_os.fetch_article", lambda url: article)
    monkeypatch.setattr("app.api.news_skill_os.analyze_fresh_link", lambda team, item, focus: {
        "event_title": "融资公告",
        "claims": [{"id": "c1", "subject": "公司", "predicate": "融资",
                    "text": "公司计划融资1亿美元", "evidence": "公司计划融资1亿美元。"}],
        "analysis": {"title": "融资公告", "body": "【北京】公司计划融资1亿美元。",
                     "warnings": [], "notes": "待核查"},
        "sources": [article["url"]],
    })
    monkeypatch.setattr("app.api.news_skill_os.research_fresh_link", lambda team, item, claims: {
        "status": "completed", "queries": ["公司计划融资1亿美元"],
        "sources": [{"id": "s2", "url": "https://other.example/notice",
                     "host": "other.example", "title": "另一份公告", "site": "另一来源",
                     "date": None}],
        "matches": [{"claim_id": "c1", "source_id": "s2", "stance": "supports",
                     "quote": "公司计划融资1亿美元。"}],
        "checks": [{"claim_id": "c1", "claim": "公司计划融资1亿美元",
                    "status": "external_match"}],
        "warnings": ["外部材料仍需人工确认。"],
    })
    editor = login(client, "editor@plus.test")
    response = client.post("/api/news-skill-os/from-link/analyze", headers=editor,
                           json={"url": article["url"]})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["analysis"]["external_research"]["status"] == "completed"
    event_id = payload["event"]["id"]
    revision = client.get(f"/api/news-skill-os/events/{event_id}/revisions/1",
                          headers=editor).json()["snapshot"]
    assert len(revision["sources"]) == 2
    assert len(revision["evidence"]) == 2
    assert revision["links"][1]["stance"] == "supports"
    assert revision["claims"][0]["status"] == "unverified"


def test_external_search_failure_does_not_block_original_link_analysis(client, monkeypatch):
    enable(monkeypatch)
    article = {"url": "https://source.example/story", "title": "公告",
               "site": "源头", "published_at": "", "text": "公司发布公告。"}
    monkeypatch.setattr("app.api.news_skill_os.fetch_article", lambda url: article)
    monkeypatch.setattr("app.api.news_skill_os.analyze_fresh_link", lambda team, item, focus: {
        "event_title": "公告",
        "claims": [{"id": "c1", "subject": "公司", "predicate": "发布",
                    "text": "公司发布公告", "evidence": "公司发布公告。"}],
        "analysis": {"title": "公告", "body": "【北京】公司发布公告。", "warnings": []},
        "sources": [article["url"]],
    })
    monkeypatch.setattr("app.api.news_skill_os.research_fresh_link",
                        lambda team, item, claims: 1 / 0)
    editor = login(client, "editor@plus.test")
    response = client.post("/api/news-skill-os/from-link/analyze", headers=editor,
                           json={"url": article["url"]})
    assert response.status_code == 201, response.text
    assert response.json()["analysis"]["external_research"]["status"] == "unavailable"
