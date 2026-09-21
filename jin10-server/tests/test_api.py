from __future__ import annotations

import hashlib
import uuid
from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Material
from tests.conftest import login

IMG_URL = "https://img.jin10.com/misc/26/09/Qth2R_ghf4ISbSp7WS-ja.jpg"
IMG_ID = hashlib.sha256(IMG_URL.encode()).hexdigest()[:32]


def material(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "source_item_id": "flash20260921164125290800",
        "fingerprint": "a" * 64,
        "item_kind": "flash",
        "title": "英债供需结构变了",
        "flash_title": "晨星：英国国债收益率高企或加大公共财政压力",
        "content": "晨星分析师指出……",
        "content_raw": "金十数据9月21日讯，晨星分析师指出……",
        "plus_title": "英债供需结构变了",
        "has_plus_link": True,
        "images": [],
        "publish_time": "2026-09-21T16:41:25+08:00",
        "url": "https://www.jin10.com/",
        "raw_html": "<div></div>",
        "collect_time": "2026-09-21T09:00:00Z",
        "collect_method": "context_menu",
        "interpretation_status": "failed",
        "interpretation": None,
    }
    base.update(overrides)
    return base


INTERPRETATION = {
    "plus_id": "6620",
    "title": "英债供需结构变了",
    "modules": [
        {
            "kind": "observation",
            "heading": "重点观察",
            "paragraphs": ["要点一"],
            "images": [{"url": IMG_URL, "source": "plus", "asset_id": IMG_ID}],
        }
    ],
    "fetched_at": "2026-09-21T09:00:00Z",
}


def push(client: TestClient, headers: dict[str, str], *items: dict[str, Any]) -> list[dict[str, Any]]:
    res = client.post("/api/materials/sync", json={"materials": list(items)}, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["results"]


class TestAuth:
    def test_login_and_me(self, client: TestClient) -> None:
        headers = login(client, "EDITOR@plus.test")  # 邮箱不区分大小写
        me = client.get("/api/auth/me", headers=headers).json()
        assert me["user"]["name"] == "编辑甲"
        assert me["team"]["name"] == "PLUS 研究组"
        assert {m["email"] for m in me["members"]} == {"admin@plus.test", "editor@plus.test", "viewer@plus.test"}

    def test_wrong_password(self, client: TestClient) -> None:
        res = client.post("/api/auth/login", json={"email": "editor@plus.test", "password": "nope"})
        assert res.status_code == 401

    def test_requires_token(self, client: TestClient) -> None:
        assert client.get("/api/materials/changes").status_code == 401
        bad = {"Authorization": "Bearer not-a-token"}
        assert client.get("/api/materials/changes", headers=bad).status_code == 401


class TestSync:
    def test_create_then_unchanged(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        m = material()
        [first] = push(client, h, m)
        assert first["outcome"] == "created"
        assert first["material"]["id"] == m["id"]
        assert len(first["material"]["collected_by"]) == 1
        [again] = push(client, h, m)
        assert again["outcome"] == "unchanged"

    def test_teammate_collects_same_flash(self, client: TestClient) -> None:
        """同事用不同的本地 ID 收藏同一条快讯：合并到团队既有素材，收藏人 +1"""
        editor, admin = login(client, "editor@plus.test"), login(client, "admin@plus.test")
        mine = material()
        push(client, editor, mine)
        [theirs] = push(client, admin, material(id=str(uuid.uuid4()), fingerprint="b" * 64))
        assert theirs["outcome"] == "merged"
        assert theirs["material"]["id"] == mine["id"]  # 客户端据此替换本地 ID
        assert len(theirs["material"]["collected_by"]) == 2

    def test_dedupe_by_fingerprint_without_item_id(self, client: TestClient) -> None:
        editor, admin = login(client, "editor@plus.test"), login(client, "admin@plus.test")
        push(client, editor, material(source_item_id=None))
        [res] = push(client, admin, material(id=str(uuid.uuid4()), source_item_id=None))
        assert res["outcome"] == "merged"

    def test_interpretation_filled_but_never_overwritten(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        m = material()
        push(client, h, m)
        [filled] = push(client, h, {**m, "interpretation_status": "fetched", "interpretation": INTERPRETATION})
        assert filled["outcome"] == "merged"
        assert filled["material"]["interpretation"]["modules"][0]["paragraphs"] == ["要点一"]
        [kept] = push(client, h, {**m, "interpretation_status": "failed", "interpretation": None})
        assert kept["material"]["interpretation_status"] == "fetched"

    def test_images_merged_by_url(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        m = material(images=[{"url": IMG_URL, "source": "flash", "asset_id": None}])
        push(client, h, m)
        [res] = push(client, h, {**m, "images": [{"url": IMG_URL, "source": "flash", "asset_id": IMG_ID}]})
        assert res["material"]["images"] == [{"url": IMG_URL, "source": "flash", "asset_id": IMG_ID}]

    def test_missing_title_and_thumbnail_fixed_by_recollect(self, client: TestClient) -> None:
        # 旧版插件：PLUS 快讯标题没识别（退化为正文首句），配图存成 /flash 缩略图链接
        h = login(client, "editor@plus.test")
        thumb = "https://flash-scdn.jin10.com/x.jpg/flash"
        old = material(title="核心判断", flash_title=None, plus_title=None, has_plus_link=False,
                       images=[{"url": thumb, "source": "flash", "asset_id": None}])
        push(client, h, old)
        new_img = {"url": "https://flash-scdn.jin10.com/x.jpg", "source": "flash", "asset_id": IMG_ID}
        [res] = push(client, h, {**old, "id": str(uuid.uuid4()), "title": "投行：美股风险", "flash_title": "投行：美股风险",
                                 "images": [new_img]})
        assert res["outcome"] == "merged"
        assert res["material"]["title"] == "投行：美股风险"
        assert res["material"]["images"] == [new_img]

    def test_server_fields_not_overwritten_by_client(
        self, client: TestClient, db_factory: sessionmaker[Session]
    ) -> None:
        h = login(client, "editor@plus.test")
        m = material()
        push(client, h, m)
        with db_factory() as db:
            row = db.get(Material, uuid.UUID(m["id"]))
            assert row is not None
            row.category, row.importance_score, row.ai_status = "macro", 88, "done"
            db.commit()
        [res] = push(client, h, {**m, "category": "ai", "importance_score": 1, "ai_status": "pending"})
        assert (res["material"]["category"], res["material"]["importance_score"]) == ("macro", 88)

    def test_delete_and_restore(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        m = material()
        push(client, h, m)
        [deleted] = push(client, h, {**m, "deleted_at": "2026-09-21T10:00:00Z"})
        assert deleted["outcome"] == "deleted"
        assert deleted["material"]["deleted_at"] is not None
        [restored] = push(client, h, {**m, "id": str(uuid.uuid4())})
        assert restored["outcome"] == "created"
        assert restored["material"]["id"] == m["id"]
        assert restored["material"]["deleted_at"] is None

    def test_viewer_cannot_push(self, client: TestClient) -> None:
        h = login(client, "viewer@plus.test")
        res = client.post("/api/materials/sync", json={"materials": [material()]}, headers=h)
        assert res.status_code == 403

    def test_delete_endpoint(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        m = material()
        push(client, h, m)
        assert client.delete(f"/api/materials/{m['id']}", headers=h).status_code == 204
        assert client.get(f"/api/materials/{m['id']}", headers=h).json()["deleted_at"] is not None


class TestChanges:
    def test_incremental_pull_with_cursor(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        items = [material(source_item_id=f"flash{i:020d}", fingerprint=f"{i:064x}") for i in range(5)]
        push(client, h, *items)

        page1 = client.get("/api/materials/changes", params={"limit": 3}, headers=h).json()
        assert len(page1["items"]) == 3 and page1["has_more"]
        page2 = client.get("/api/materials/changes", params={"limit": 3, "cursor": page1["next_cursor"]}, headers=h).json()
        assert len(page2["items"]) == 2 and not page2["has_more"]
        seen = {i["id"] for i in page1["items"] + page2["items"]}
        assert seen == {i["id"] for i in items}

        # 之后只返回新变更（包括删除）
        push(client, h, {**items[0], "deleted_at": "2026-09-21T10:00:00Z"})
        page3 = client.get("/api/materials/changes", params={"cursor": page2["next_cursor"]}, headers=h).json()
        assert [i["id"] for i in page3["items"]] == [items[0]["id"]]
        assert page3["items"][0]["deleted_at"] is not None

    def test_team_isolation(self, client: TestClient) -> None:
        m = material()
        push(client, login(client, "editor@plus.test"), m)
        out = login(client, "out@plus.test")
        assert client.get("/api/materials/changes", headers=out).json()["items"] == []
        assert client.get(f"/api/materials/{m['id']}", headers=out).status_code == 404
        # 另一团队采集同一条快讯互不影响
        [res] = push(client, out, material(id=str(uuid.uuid4())))
        assert res["outcome"] == "created"

    def test_bad_cursor(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        assert client.get("/api/materials/changes", params={"cursor": "garbage"}, headers=h).status_code == 400


class TestAssets:
    def _put(self, client: TestClient, h: dict[str, str], asset_id: str = IMG_ID, url: str = IMG_URL, mime: str = "image/jpeg"):
        return client.put(
            f"/api/assets/{asset_id}",
            content=b"\xff\xd8fake-jpeg",
            headers={**h, "Content-Type": mime, "X-Source-Url": quote(url, safe="")},
        )

    def test_upload_exists_download(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        assert client.post("/api/assets/exists", json={"ids": [IMG_ID]}, headers=h).json()["missing"] == [IMG_ID]
        assert self._put(client, h).status_code == 201
        assert self._put(client, h).status_code == 200  # 幂等
        assert client.post("/api/assets/exists", json={"ids": [IMG_ID]}, headers=h).json()["missing"] == []
        res = client.get(f"/api/assets/{IMG_ID}", headers=h)
        assert res.status_code == 200 and res.content == b"\xff\xd8fake-jpeg"
        assert res.headers["content-type"] == "image/jpeg"

    def test_id_must_match_source_url(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        assert self._put(client, h, asset_id="0" * 32).status_code == 400

    def test_rejects_non_image(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        assert self._put(client, h, mime="text/html").status_code == 415

    def test_assets_are_team_scoped(self, client: TestClient) -> None:
        self._put(client, login(client, "editor@plus.test"))
        assert client.get(f"/api/assets/{IMG_ID}", headers=login(client, "out@plus.test")).status_code == 404
