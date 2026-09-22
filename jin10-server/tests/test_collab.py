from __future__ import annotations

import datetime as dt
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Material
from app.services.maintenance import purge
from tests.conftest import login
from tests.test_api import material, push


class TestRecycleBin:
    def test_delete_then_restore(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        m = material()
        push(client, h, m)
        client.post("/api/materials/bulk-delete", json={"ids": [m["id"]]}, headers=h)
        deleted = client.get("/api/materials/deleted", headers=h).json()
        assert [d["id"] for d in deleted] == [m["id"]]
        assert client.post("/api/materials/restore", json={"ids": [m["id"]]}, headers=h).json() == {"deleted": 1}
        assert client.get("/api/materials/deleted", headers=h).json() == []
        assert client.get(f"/api/materials/{m['id']}", headers=h).json()["deleted_at"] is None


class TestPresence:
    def test_colleague_sees_who_is_editing(self, client: TestClient) -> None:
        editor = login(client, "editor@plus.test")
        admin = login(client, "admin@plus.test")
        assert client.post("/api/presence", json={"material_id": "m1"}, headers=editor).json() == []
        seen = client.post("/api/presence", json={"material_id": None}, headers=admin).json()
        assert seen == [{"material_id": "m1", "user_name": "编辑甲"}]
        client.post("/api/presence", json={"material_id": None}, headers=editor)
        assert client.post("/api/presence", json={}, headers=admin).json() == []


class TestParseReports:
    def test_report_and_summary(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        for kind in ("image_failed", "image_failed", "unrecognized_title"):
            res = client.post("/api/parse-reports", json={"kind": kind, "detail": "x", "html": "<div/>"}, headers=h)
            assert res.status_code == 204
        summary = client.get("/api/parse-reports/summary", headers=h).json()
        assert summary["total"] == 3
        assert summary["by_kind"] == {"image_failed": 2, "unrecognized_title": 1}
        assert summary["latest"][0]["user_name"] == "编辑甲"


class TestRetention:
    def test_purges_materials_older_than_retention(
        self, client: TestClient, db_factory: sessionmaker[Session]
    ) -> None:
        h = login(client, "editor@plus.test")
        old = material(publish_time="2026-09-01T10:00:00+08:00")
        fresh = material(source_item_id="flash20260921164125290801", fingerprint="b" * 64)
        push(client, h, old, fresh)
        with db_factory() as db:
            result = purge(db, 7, now=dt.datetime(2026, 9, 22, tzinfo=dt.UTC))
            assert result["materials"] == 1
            assert db.get(Material, uuid.UUID(old["id"])) is None
            assert db.get(Material, uuid.UUID(fresh["id"])) is not None
