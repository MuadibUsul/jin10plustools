"""Live search leads must be fetched and quoted before they become evidence."""
from __future__ import annotations

from app.news_skill_os import fresh_research


ARTICLE = {"url": "https://x.com/source/status/1", "title": "Source on X",
           "text": "公司公告称融资1亿美元。"}
CLAIMS = [{"id": "c1", "text": "公司融资1亿美元", "evidence": "公司公告称融资1亿美元。"}]


def test_missing_deepseek_key_keeps_original_analysis_available(monkeypatch):
    monkeypatch.setattr(fresh_research, "provider", lambda team, name: {"key": ""})
    result = fresh_research.research(None, ARTICLE, CLAIMS)
    assert result["status"] == "unavailable"
    assert result["matches"] == []
    assert "未配置" in result["warnings"][0]


def test_search_snippet_is_not_evidence_and_only_literal_fetched_quote_passes(monkeypatch):
    calls = []
    content = "一手公告：公司计划融资1亿美元，用于建设项目。" + "公告内容详见正文。" * 8

    def fake_post(key, path, body, **kwargs):
        calls.append((path, body))
        if path.endswith("/search"):
            return {"results": [
                {"url": ARTICLE["url"], "title": "原始来源", "snippet": "公司融资1亿美元"},
                {"url": "https://official.example/notice", "title": "公告", "site_name": "官方公告",
                 "snippet": "公司融资1亿美元"},
                {"url": "https://media.example/story", "title": "报道", "site_name": "媒体",
                 "snippet": "公司融资2亿美元"},
            ]}
        return {"title": "完整网页", "content": content}

    monkeypatch.setattr(fresh_research, "provider", lambda team, name: {"key": "test"})
    monkeypatch.setattr(fresh_research, "_post", fake_post)
    monkeypatch.setattr(fresh_research, "_public_url", lambda url: url)
    monkeypatch.setattr(fresh_research, "_review_matches", lambda team, claims, pages: [
        {"claim_id": "c1", "source_id": "s2", "stance": "supports",
         "quote": "公司计划融资1亿美元，用于建设项目。"},
        {"claim_id": "c1", "source_id": "s3", "stance": "contradicts",
         "quote": "搜索摘要说公司融资2亿美元"},
    ])
    result = fresh_research.research(None, ARTICLE, CLAIMS)
    assert result["status"] == "completed"
    assert result["checks"][0]["status"] == "external_match"
    assert len(result["matches"]) == 1
    assert result["matches"][0]["quote"] == "公司计划融资1亿美元，用于建设项目。"
    assert all(item["url"] != ARTICLE["url"] for item in result["sources"])
    assert len([path for path, _ in calls if path.endswith("/fetch")]) == 2


def test_supporting_quote_with_wrong_number_is_rejected(monkeypatch):
    content = "公告表示公司将融资2亿美元。" + "这是一个公开的完整网页。" * 8
    monkeypatch.setattr(fresh_research, "provider", lambda team, name: {"key": "test"})
    monkeypatch.setattr(fresh_research, "_public_url", lambda url: url)
    monkeypatch.setattr(fresh_research, "_post", lambda key, path, body, **kwargs: (
        {"results": [{"url": "https://media.example/story", "title": "报道"}]}
        if path.endswith("/search") else {"content": content}
    ))
    monkeypatch.setattr(fresh_research, "_review_matches", lambda team, claims, pages: [
        {"claim_id": "c1", "source_id": "s2", "stance": "supports",
         "quote": "公告表示公司将融资2亿美元。"},
    ])
    result = fresh_research.research(None, ARTICLE, CLAIMS)
    assert result["matches"] == []
    assert result["checks"][0]["status"] == "unverified"
