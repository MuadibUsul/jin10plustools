from __future__ import annotations

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
        assert board == {"day": DAY, "picked": [], "order": {}, "version": 0, "updated_by": None, "updated_at": None}

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
