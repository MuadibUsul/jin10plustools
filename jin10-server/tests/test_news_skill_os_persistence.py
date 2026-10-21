"""Persistent, team-scoped News Skill OS facts, drafts and workflow runs.

No network, no paid model and no publication path is exercised here.
"""

import json

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import (
    BriefJob, Issue, Material, NewsAuditEvent, NewsDraft, NewsDraftRevision, NewsEvent,
    NewsEventClaim, NewsEventRevision, NewsSkillVersion, NewsWorkflowNode, NewsWorkflowRun,
)
from tests.conftest import login


def enable(monkeypatch):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()


def event_body(**overrides):
    body = {"title": "央行发布公告", "event_time": "2026-10-20T09:00:00+08:00",
            "event_key": "pbc-20261020", "payload": {"topic": "monetary"}}
    body.update(overrides)
    return body


def revision_body(**overrides):
    body = {
        "expected_previous": 0,
        "sources": [{"id": "s1", "name": "中国人民银行", "kind": "official",
                     "original_id": "pbc-origin-1"}],
        "evidence": [{"id": "e1", "source_id": "s1", "excerpt": "公告原文",
                      "captured_at": "2026-10-20T09:05:00+08:00"}],
        "claims": [{"id": "c1", "subject": "央行", "predicate": "操作",
                    "text": "开展逆回购", "status": "verified"}],
        "links": [{"claim_id": "c1", "evidence_id": "e1", "stance": "supports"}],
        "analyses": [{"id": "a1", "section": "前因", "text": "维护流动性", "label": "inference",
                      "claim_ids": ["c1"]}],
        "reason": "首次入库",
    }
    body.update(overrides)
    return body


def counts(factory):
    models = (Material, Issue, BriefJob, NewsEvent, NewsEventRevision, NewsEventClaim,
              NewsDraft, NewsDraftRevision, NewsSkillVersion, NewsWorkflowRun, NewsWorkflowNode,
              NewsAuditEvent)
    with factory() as db:
        return {model.__name__: int(db.scalar(select(func.count()).select_from(model)) or 0)
                for model in models}


def create_event(client, headers, **overrides):
    response = client.post("/api/news-skill-os/events", headers=headers, json=event_body(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_flag_off_writes_nothing(client, db_factory, monkeypatch):
    monkeypatch.delenv("NEWS_SKILL_OS_ENABLED", raising=False)
    monkeypatch.delenv("PLUS_NEWS_SKILL_OS_ENABLED", raising=False)
    get_settings.cache_clear()
    before = counts(db_factory)
    response = client.post("/api/news-skill-os/events", headers=login(client, "editor@plus.test"),
                           json=event_body())
    assert response.status_code == 404
    assert counts(db_factory) == before
    get_settings.cache_clear()


def test_editor_appends_immutable_revisions_and_viewer_reads(client, db_factory, monkeypatch):
    enable(monkeypatch)
    editor = login(client, "editor@plus.test")
    viewer = login(client, "viewer@plus.test")
    event = create_event(client, editor)
    event_id = event["id"]

    first = client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=editor,
                        json=revision_body())
    assert first.status_code == 201, first.text
    assert first.json()["number"] == 1

    correction = revision_body(
        expected_previous=1,
        claims=[
            {"id": "c1", "subject": "央行", "predicate": "操作", "text": "开展逆回购",
             "status": "superseded", "superseded_by": "c2"},
            {"id": "c2", "subject": "央行", "predicate": "操作", "text": "开展 500 亿元逆回购",
             "status": "unverified"},
        ],
        links=[
            {"claim_id": "c1", "evidence_id": "e1", "stance": "supports"},
            {"claim_id": "c2", "evidence_id": "e1", "stance": "supports"},
        ],
        analyses=[],
        reason="补充金额",
    )
    second = client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=editor,
                         json=correction)
    assert second.status_code == 201, second.text
    assert second.json()["number"] == 2

    old = client.get(f"/api/news-skill-os/events/{event_id}/revisions/1", headers=viewer)
    assert old.status_code == 200
    assert old.json()["snapshot"]["claims"][0]["status"] == "verified"
    latest = client.get(f"/api/news-skill-os/events/{event_id}/revisions/2", headers=viewer)
    assert latest.json()["snapshot"]["claims"][0]["status"] == "superseded"
    detail = client.get(f"/api/news-skill-os/events/{event_id}", headers=viewer).json()
    assert [item["number"] for item in detail["revisions"]] == [1, 2]

    assert client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=viewer,
                       json=revision_body(expected_previous=2)).status_code == 403
    assert client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=editor,
                       json=revision_body(expected_previous=0)).status_code == 409


def test_claim_disappearance_is_a_conflict(client, db_factory, monkeypatch):
    enable(monkeypatch)
    editor = login(client, "editor@plus.test")
    event_id = create_event(client, editor)["id"]
    assert client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=editor,
                       json=revision_body()).status_code == 201
    body = revision_body(expected_previous=1, claims=[], links=[], analyses=[],
                         reason="删除事实")
    response = client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=editor, json=body)
    assert response.status_code == 409
    assert "disappear" in response.json()["detail"]


def test_team_isolation_and_event_validation(client, db_factory, monkeypatch):
    enable(monkeypatch)
    editor = login(client, "editor@plus.test")
    outsider = login(client, "out@plus.test")
    event = create_event(client, editor)
    assert client.get(f"/api/news-skill-os/events/{event['id']}", headers=outsider).status_code == 404
    assert client.post(f"/api/news-skill-os/events/{event['id']}/revisions", headers=outsider,
                       json=revision_body()).status_code == 404
    assert client.get("/api/news-skill-os/events", headers=outsider).json()["items"] == []
    assert client.post("/api/news-skill-os/events", headers=login(client, "viewer@plus.test"),
                       json=event_body()).status_code == 403
    assert client.post("/api/news-skill-os/events", headers=editor,
                       json=event_body(event_time="2026-10-20T09:00:00")).status_code == 422
    assert client.post("/api/news-skill-os/events", headers=editor,
                       json=event_body(event_key="pbc-20261020")).status_code == 409
    assert client.get("/api/news-skill-os/skills", headers=login(client, "viewer@plus.test")).status_code == 200


def test_draft_revision_review_and_audit(client, db_factory, monkeypatch):
    enable(monkeypatch)
    editor = login(client, "editor@plus.test")
    viewer = login(client, "viewer@plus.test")
    event_id = create_event(client, editor)["id"]
    assert client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=editor,
                       json=revision_body()).status_code == 201
    created = client.post("/api/news-skill-os/drafts", headers=editor, json={
        "title": "央行开展逆回购", "body": "央行今日开展 500 亿元逆回购。",
        "target": "fresh", "event_id": event_id, "event_revision": 1,
        "skill_id": "news.fixed-volume", "skill_version": "0.1.0",
    })
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["publication_allowed"] is False and draft["status"] == "draft"

    revised = client.post(f"/api/news-skill-os/drafts/{draft['id']}/revisions", headers=editor, json={
        "expected_revision": 1, "title": "央行开展逆回购（修订）",
        "body": "央行今日开展 500 亿元逆回购，利率持平。", "event_revision": 1,
    })
    assert revised.status_code == 201, revised.text
    assert revised.json()["number"] == 2
    assert client.post(f"/api/news-skill-os/drafts/{draft['id']}/revisions", headers=editor, json={
        "expected_revision": 1, "title": "过期修订", "body": "旧版本",
    }).status_code == 409
    assert client.post(f"/api/news-skill-os/drafts/{draft['id']}/review", headers=editor,
                       json={"decision": "reject"}).status_code == 422
    rejected = client.post(f"/api/news-skill-os/drafts/{draft['id']}/review", headers=editor,
                           json={"decision": "reject", "note": "金额需补来源"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    approved = client.post(f"/api/news-skill-os/drafts/{draft['id']}/review", headers=editor,
                           json={"decision": "approve", "note": "已复核"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.post(f"/api/news-skill-os/drafts/{draft['id']}/review", headers=editor,
                       json={"decision": "approve"}).status_code == 409
    detail = client.get(f"/api/news-skill-os/drafts/{draft['id']}", headers=viewer)
    assert detail.status_code == 200
    revisions = detail.json()["revisions"]
    assert [item["number"] for item in revisions] == [1, 2]
    assert revisions[1]["review_status"] == "approved" and revisions[1]["review_note"] == "已复核"
    audit = client.get("/api/news-skill-os/audit", headers=viewer,
                       params={"entity_type": "draft", "entity_id": draft["id"]}).json()["items"]
    assert {item["action"] for item in audit} >= {"draft.create", "draft.revision", "draft.approve"}
    assert client.post("/api/news-skill-os/drafts", headers=editor, json={
        "title": "无 Skill", "body": "正文", "skill_id": "news.fixed-volume",
        "skill_version": "9.9.9",
    }).status_code == 422


def test_event_workflow_uses_persisted_fact_chain(client, db_factory, monkeypatch):
    enable(monkeypatch)
    editor = login(client, "editor@plus.test")
    viewer = login(client, "viewer@plus.test")
    event_id = create_event(client, editor)["id"]
    assert client.post(f"/api/news-skill-os/events/{event_id}/revisions", headers=editor,
                       json=revision_body()).status_code == 201
    response = client.post(f"/api/news-skill-os/events/{event_id}/workflows/research/runs",
                           headers=editor, json={"revision": 1})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["event_id"] == event_id
    assert body["run"]["event_revision"] == 1
    assert body["run"]["status"] == "succeeded"
    assert body["publication_allowed"] is False
    assert body["result"]["outputs"]["analysis"]["sections"]["前因"]
    detail = client.get(f"/api/news-skill-os/workflow-runs/{body['run']['id']}", headers=viewer)
    assert detail.status_code == 200 and detail.json()["run"]["event_revision"] == 1
    empty = create_event(client, editor, event_key="empty-event")["id"]
    assert client.post(f"/api/news-skill-os/events/{empty}/workflows/research/runs",
                       headers=editor, json={}).status_code == 409
    draft = client.post(f"/api/news-skill-os/workflow-runs/{body['run']['id']}/drafts",
                        headers=editor, json={"target": "annotation"})
    assert draft.status_code == 201, draft.text
    generated = draft.json()
    assert generated["event_id"] == event_id and generated["target"] == "annotation"
    assert generated["skill_id"] == "finance.causal-analysis"
    assert "【前因】" in generated["body"] and generated["status"] == "draft"
    assert generated["publication_allowed"] is False
    assert client.post(f"/api/news-skill-os/workflow-runs/{body['run']['id']}/drafts",
                       headers=login(client, "viewer@plus.test"),
                       json={"target": "fresh"}).status_code == 403


def test_workflow_run_is_persisted_without_publishing(client, db_factory, monkeypatch):
    enable(monkeypatch)
    editor = login(client, "editor@plus.test")
    before = counts(db_factory)
    response = client.post("/api/news-skill-os/workflows/research/runs", headers=editor,
                           json={"fixture": "official"})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["run"]["status"] == "succeeded"
    assert payload["publication_allowed"] is False
    after = counts(db_factory)
    assert after["NewsWorkflowRun"] == 1 and after["NewsWorkflowNode"] >= 4
    assert after["NewsSkillVersion"] >= 4
    assert after["Material"] == before["Material"]
    detail = client.get(f"/api/news-skill-os/workflow-runs/{payload['run']['id']}", headers=editor)
    assert detail.status_code == 200 and len(detail.json()["nodes"]) >= 4
    assert client.get("/api/news-skill-os/workflow-runs", headers=login(client, "viewer@plus.test")).status_code == 200


def test_export_registers_news_tables_and_keeps_team_scope(client, db_factory, monkeypatch):
    enable(monkeypatch)
    editor = login(client, "editor@plus.test")
    create_event(client, editor)
    datasets = client.get("/api/export/datasets", headers=login(client, "viewer@plus.test")).json()
    keys = [item["key"] for item in datasets["datasets"]]
    for key in ("news_events", "news_event_revisions", "news_event_claims", "news_drafts",
                "news_skill_versions", "news_workflow_runs", "news_audit_events"):
        assert key in keys
    assert len(keys) == 25
    body = client.get("/api/export/news_events", headers=editor,
                      params={"start": "2026-10-20", "end": "2026-10-20", "format": "json"}).json()
    assert [item["title"] for item in body] == ["央行发布公告"]
    assert "raw_html" not in json.dumps(body, ensure_ascii=False)
    outsider = login(client, "out@plus.test")
    assert client.get("/api/export/news_events", headers=outsider,
                      params={"start": "2026-10-20", "end": "2026-10-20", "format": "json"}).json() == []
    get_settings.cache_clear()
