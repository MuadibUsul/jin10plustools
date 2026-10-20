from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("PLUS_JWT_SECRET", "test-secret-please-ignore-0123456789")
os.environ["PLUS_AI_WORKER_ENABLED"] = "false"  # 测试中绝不调用真实 JEV

from app.config import get_settings  # noqa: E402
from app.db.models import Base, Role, Team, User  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture
def db_factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sessionmaker[Session]]:
    monkeypatch.setenv("PLUS_STORAGE_DIR", str(tmp_path / "storage"))
    get_settings.cache_clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        # PostgreSQL enforces FKs; keep the SQLite test double honest as well.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db] = override
    yield factory
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def seed(db_factory: sessionmaker[Session]) -> dict[str, User]:
    """两个团队：研究组（管理员、编辑、只读）与另一团队（编辑）"""
    with db_factory() as db:
        team = Team(name="PLUS 研究组")
        other = Team(name="其他团队")
        db.add_all([team, other])
        db.flush()
        users = {
            "admin": User(team_id=team.id, email="admin@plus.test", name="管理员", role=Role.ADMIN),
            "editor": User(team_id=team.id, email="editor@plus.test", name="编辑甲", role=Role.EDITOR),
            "viewer": User(team_id=team.id, email="viewer@plus.test", name="只读", role=Role.VIEWER),
            "outsider": User(team_id=other.id, email="out@plus.test", name="外部", role=Role.EDITOR),
        }
        for u in users.values():
            u.password_hash = hash_password(PASSWORD)
        db.add_all(users.values())
        db.commit()
        return users


@pytest.fixture
def client(seed: dict[str, User]) -> TestClient:
    return TestClient(app)


def login(client: TestClient, email: str) -> dict[str, str]:
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
