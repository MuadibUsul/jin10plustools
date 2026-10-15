from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import login

DAY = "2026-09-22"


def ops(client: TestClient, headers: dict[str, str], **body: object) -> dict:
    res = client.post(f"/api/board/ops?day={DAY}", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


class TestBoard:
    def test_empty_board(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        board = client.get(f"/api/board?day={DAY}", headers=h).json()
        assert board == {"day": DAY, "picked": [], "order": {}, "cover_ids": None, "version": 0, "updated_by": None, "updated_at": None}

    def test_two_accounts_share_and_merge(self, client: TestClient) -> None:
        editor = login(client, "editor@plus.test")
        admin = login(client, "admin@plus.test")
        ops(client, editor, pick_add=["a"])
        # 另一账号基于旧状态操作，增量合并而不是覆盖
        ops(client, admin, pick_add=["b"])
        ops(client, editor, pick_remove=["a"], order={"macro": ["x", "y"]})
        board = client.get(f"/api/board?day={DAY}", headers=admin).json()
        assert board["picked"] == ["b"]
        assert board["order"] == {"macro": ["x", "y"]}
        assert board["version"] == 3

    def test_move_between_sections_and_reset(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        ops(client, h, order={"macro": ["x", "y"]})
        board = ops(client, h, order={"ai": ["y"], "macro": ["x"]})
        assert board["order"] == {"macro": ["x"], "ai": ["y"]}
        board = ops(client, h, pick_set=["p", "q", "p"], order_reset=True)
        assert board["picked"] == ["p", "q"] and board["order"] == {}

    def test_viewer_read_only_and_days_isolated(self, client: TestClient) -> None:
        h = login(client, "editor@plus.test")
        ops(client, h, pick_add=["a"])
        viewer = login(client, "viewer@plus.test")
        assert client.post(f"/api/board/ops?day={DAY}", json={"pick_add": ["z"]}, headers=viewer).status_code == 403
        assert client.get(f"/api/board?day={DAY}", headers=viewer).json()["picked"] == ["a"]
        assert client.get("/api/board?day=2026-09-23", headers=h).json()["picked"] == []


def test_cover_selection_is_shared_limited_and_preserved(client, db_factory):
    from app.db.models import Material
    from tests.test_api import material, push

    h = login(client, "editor@plus.test")
    items = [material(source_item_id=f"cover{i}", fingerprint=f"{i:064x}") for i in range(15)]
    push(client, h, *items)
    ids = [item['id'] for item in items]
    with db_factory() as db:
        for index, mid in enumerate(ids):
            m = db.get(Material, uuid.UUID(mid))
            m.category = 'macro'
            m.importance_score = index
        db.commit()
    ops(client, h, pick_set=ids)
    # 推荐为最后 12 条，首次取消后进入人工模式，不自动补足。
    board = ops(client, h, cover_remove=[ids[-1]])
    assert len(board['cover_ids']) == 11 and ids[-1] not in board['cover_ids']
    board = ops(client, login(client, 'admin@plus.test'), cover_add=[ids[0]])
    assert len(board['cover_ids']) == 12 and ids[0] in board['cover_ids']
    assert client.post(f'/api/board/ops?day={DAY}', headers=h, json={'cover_add': [ids[1]]}).status_code == 400
    # 普通排序操作、另一编辑取消标题，均不重置人工精选。
    board = ops(client, h, order={'macro': list(reversed(ids))})
    assert ids[0] in board['cover_ids'] and ids[-1] not in board['cover_ids']
    board = ops(client, h, pick_remove=[ids[0]])
    assert len(board['cover_ids']) == 11 and ids[0] not in board['cover_ids']
    assert ops(client, h, cover_reset=True)['cover_ids'] is None


def test_cover_cannot_include_unpicked_or_other_team_material(client):
    h = login(client, 'editor@plus.test')
    res = client.post(f'/api/board/ops?day={DAY}', headers=h, json={'cover_add': [str(uuid.uuid4())]})
    assert res.status_code == 400
    assert client.get(f'/api/board?day={DAY}', headers=h).json()['cover_ids'] is None
