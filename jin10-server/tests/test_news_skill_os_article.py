"""Article ingestion creates unverified proposals, never verified facts."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import NewsEvent, NewsEventClaim, NewsEventEvidence
from app.news_skill_os.article_ai import ArticleExtractionError
from tests.conftest import login


def enable(monkeypatch):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()


def article_body():
    return {
        "title": "某公司发布公告", "event_time": "2026-10-20T09:00:00+08:00",
        "event_key": "article-20261020",
        "article": "公司公告显示，拟融资1亿美元，用于项目建设。管理层表示项目将分阶段推进。",
        "source_name": "公司公告", "source_kind": "official",
        "source_url": "https://example.test/announcement",
    }


def test_article_creates_unverified_claims_and_evidence(client, db_factory, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr("app.api.news_skill_os.extract_article", lambda *args: {
        "title": "某公司拟融资1亿美元",
        "claims": [{"id": "c1", "subject": "某公司", "predicate": "融资金额",
                    "text": "拟融资1亿美元", "evidence": "拟融资1亿美元",
                    "numeric": {"value": 100, "kind": "money", "unit": "million",
                                "currency": "USD"}}],
    })
    editor = login(client, "editor@plus.test")
    response = client.post("/api/news-skill-os/events/from-article", headers=editor, json=article_body())
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["claim_count"] == 1 and payload["publication_allowed"] is False
    event_id = payload["event"]["id"]
    detail = client.get(f"/api/news-skill-os/events/{event_id}", headers=editor).json()
    assert detail["event"]["current_revision"] == 1
    snapshot = client.get(f"/api/news-skill-os/events/{event_id}/revisions/1", headers=editor).json()["snapshot"]
    assert snapshot["claims"][0]["status"] == "unverified"
    assert snapshot["evidence"][0]["excerpt"] == "拟融资1亿美元"
    assert snapshot["sources"][0]["original_id"] == "https://example.test/announcement"
    with db_factory() as db:
        assert len(db.scalars(select(NewsEvent)).all()) == 1
        assert len(db.scalars(select(NewsEventClaim)).all()) == 1
        assert len(db.scalars(select(NewsEventEvidence)).all()) == 1


def test_article_permissions_and_extraction_errors(client, db_factory, monkeypatch):
    enable(monkeypatch)
    viewer = login(client, "viewer@plus.test")
    editor = login(client, "editor@plus.test")
    assert client.post("/api/news-skill-os/events/from-article", headers=viewer,
                       json=article_body()).status_code == 403
    monkeypatch.setattr("app.api.news_skill_os.extract_article",
                        lambda *args: (_ for _ in ()).throw(ArticleExtractionError("新闻稿中没有提取到可核验断言")))
    response = client.post("/api/news-skill-os/events/from-article", headers=editor, json=article_body())
    assert response.status_code == 422
    with db_factory() as db:
        assert db.scalars(select(NewsEvent)).all() == []
