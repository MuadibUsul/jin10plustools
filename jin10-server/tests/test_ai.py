from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.ai import worker
from app.ai.analysis import (
    QUESTIONS,
    ScoringConfig,
    build_state,
    final_score,
    importance_score,
    level_for,
)
from app.ai.jev_client import JevClient, JevError
from app.db.models import Material
from tests.conftest import login
from tests.test_api import INTERPRETATION, material, push

NOW = dt.datetime(2026, 9, 21, 12, 0, tzinfo=dt.UTC)


def answers(section: str = "ai", impact: float = 2.0, novelty: float = 1.5, value: float = 2.0, policy: float = 0.0,
            confidence: float = 0.9) -> dict[str, Any]:
    legend4 = {str(i): "" for i in range(4)}
    return {
        "section": {"type": "choice", "choice": section, "confidence": confidence, "probabilities": {section: 1.0}},
        "impact": {"type": "score", "score": impact, "legend": legend4, "confidence": 0.9},
        "novelty": {"type": "score", "score": novelty, "legend": legend4, "confidence": 0.9},
        "value": {"type": "score", "score": value, "legend": legend4, "confidence": 0.9},
        "policy": {"type": "noul", "noul": policy},
    }


def jev_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class TestScoring:
    def test_importance_is_weighted_and_bounded(self) -> None:
        cfg = ScoringConfig()
        assert importance_score(answers(impact=3, novelty=3, value=3, policy=1), cfg) == 100
        assert importance_score(answers(impact=0, novelty=0, value=0), cfg) == 0
        # 0.4*2/3 + 0.3*1.5/3 + 0.3*2/3 = 0.6167
        assert importance_score(answers(), cfg) == 62

    def test_final_score_blends_heat_timeliness_completeness(self) -> None:
        m = Material(
            importance_score=80, collected_by=["a", "b"], publish_time=NOW - dt.timedelta(hours=2),
            collect_time=NOW, interpretation_status="fetched", interpretation=None, images=[],
        )
        # 0.6*80 + 0.2*50 + 0.1*100 + 0.1*60 = 74
        assert final_score(m, ScoringConfig(), NOW) == 74

    def test_levels_and_team_overrides(self) -> None:
        cfg = ScoringConfig.from_team_settings({"scoring": {"level_s": 90, "unknown_key": 1}})
        assert cfg.level_s == 90
        assert [level_for(s, cfg) for s in (95, 85, 72, 50, 10)] == ["S", "A", "A", "B", "C"]

    def test_state_contains_original_text_only(self) -> None:
        m = Material(title="英债供需结构变了", flash_title="晨星：英国国债收益率高企", tags=["VIP"],
                     content="晨星分析师指出……", interpretation=INTERPRETATION, images=[])
        state = build_state(m)
        assert "英债供需结构变了" in state and "晨星分析师指出……" in state
        assert "重点观察：要点一" in state and "配图：1 张" in state


class TestJevClient:
    def test_request_shape_and_answers(self) -> None:
        seen: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["auth"] = req.headers["Authorization"]
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"model": "jev-1.13.0", "answers": answers()})

        client = JevClient("k", transport=jev_transport(handler))
        result = client.evaluate("状态", QUESTIONS)
        assert result["section"]["choice"] == "ai"
        assert seen["auth"] == "Bearer k"
        assert seen["body"]["model"] == "jev-latest" and set(seen["body"]["questions"]) == set(QUESTIONS)

    def test_retries_then_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.ai.jev_client.time.sleep", lambda _s: None)
        calls = {"n": 0}

        def handler(_req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(429, text="rate limited")

        with pytest.raises(JevError) as err:
            JevClient("k", transport=jev_transport(handler), max_retries=2).evaluate("s", QUESTIONS)
        assert err.value.retryable and calls["n"] == 3

    def test_client_error_not_retried(self) -> None:
        calls = {"n": 0}

        def handler(_req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401, text="bad key")

        with pytest.raises(JevError) as err:
            JevClient("k", transport=jev_transport(handler)).evaluate("s", QUESTIONS)
        assert not err.value.retryable and calls["n"] == 1


class TestWorker:
    def _pending(self, client: TestClient, **overrides: Any) -> str:
        h = login(client, "editor@plus.test")
        m = material(**overrides)
        push(client, h, m)
        return m["id"]

    def test_new_material_is_queued_then_analyzed(self, client: TestClient, db_factory: sessionmaker[Session]) -> None:
        mid = self._pending(client)
        jev = JevClient("k", transport=jev_transport(lambda _r: httpx.Response(200, json={"answers": answers()})))
        assert worker.run_once(db_factory, jev) == 1

        h = login(client, "editor@plus.test")
        out = client.get(f"/api/materials/{mid}", headers=h).json()
        assert out["ai_status"] == "done" and out["category"] == "ai"
        assert out["importance_score"] == 62 and out["level"] in "SABC"
        assert "栏目 AI热点" in out["ai_reason"]
        # 分析结果会出现在增量同步中
        changes = client.get("/api/materials/changes", headers=h).json()["items"]
        assert changes[0]["ai_status"] == "done"
        # 文字原样保留
        assert out["title"] == "英债供需结构变了" and out["content"] == "晨星分析师指出……"

    def test_analyze_requested_goes_first(self, client: TestClient, db_factory: sessionmaker[Session]) -> None:
        self._pending(client, source_item_id="flash1", fingerprint="1" * 64)
        urgent = self._pending(client, source_item_id="flash2", fingerprint="2" * 64, analyze_requested=True)
        with db_factory() as db:
            assert worker.claim_batch(db, 1) == [uuid.UUID(urgent)]

    def test_retryable_error_requeues_until_limit(
        self, client: TestClient, db_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.ai.jev_client.time.sleep", lambda _s: None)
        mid = uuid.UUID(self._pending(client))
        jev = JevClient("k", max_retries=0, transport=jev_transport(lambda _r: httpx.Response(503)))
        statuses = []
        for _ in range(3):
            worker.run_once(db_factory, jev)
            with db_factory() as db:
                statuses.append(db.get(Material, mid).ai_status)  # type: ignore[union-attr]
        assert statuses == ["pending", "pending", "failed"]

    def test_interpretation_filled_triggers_reanalysis(
        self, client: TestClient, db_factory: sessionmaker[Session]
    ) -> None:
        h = login(client, "editor@plus.test")
        m = material()
        push(client, h, m)
        jev = JevClient("k", transport=jev_transport(lambda _r: httpx.Response(200, json={"answers": answers()})))
        worker.run_once(db_factory, jev)
        [res] = push(client, h, {**m, "interpretation_status": "fetched", "interpretation": INTERPRETATION})
        assert res["material"]["ai_status"] == "pending"

    def test_new_collector_only_refreshes_score(self, client: TestClient, db_factory: sessionmaker[Session]) -> None:
        m = material()
        push(client, login(client, "editor@plus.test"), m)
        jev = JevClient("k", transport=jev_transport(lambda _r: httpx.Response(200, json={"answers": answers()})))
        worker.run_once(db_factory, jev)
        before = client.get(f"/api/materials/{m['id']}", headers=login(client, "editor@plus.test")).json()
        [res] = push(client, login(client, "admin@plus.test"), {**m, "id": str(uuid.uuid4())})
        assert res["material"]["ai_status"] == "done"
        assert res["material"]["final_score"] > before["final_score"]

    def test_manual_reanalyze_endpoint(self, client: TestClient, db_factory: sessionmaker[Session]) -> None:
        mid = self._pending(client)
        jev = JevClient("k", transport=jev_transport(lambda _r: httpx.Response(200, json={"answers": answers()})))
        worker.run_once(db_factory, jev)
        h = login(client, "editor@plus.test")
        res = client.post(f"/api/materials/{mid}/analyze", headers=h).json()
        assert res["ai_status"] == "pending" and res["analyze_requested"] is True
        assert client.post(f"/api/materials/{mid}/analyze", headers=login(client, "viewer@plus.test")).status_code == 403
