"""Feature flag and permission checks against the existing FastAPI app."""

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import BriefJob, Issue, Material
from tests.conftest import login


def _counts(factory):
    with factory() as db:
        return tuple(int(db.scalar(select(func.count()).select_from(model)) or 0)
                     for model in (Material, Issue, BriefJob))


def test_flag_defaults_off_and_preview_is_inert(client, db_factory, monkeypatch):
    monkeypatch.delenv("NEWS_SKILL_OS_ENABLED", raising=False)
    monkeypatch.delenv("PLUS_NEWS_SKILL_OS_ENABLED", raising=False)
    get_settings.cache_clear()
    assert get_settings().news_skill_os_enabled is False
    headers = login(client, "editor@plus.test")
    before = _counts(db_factory)
    response = client.post("/api/news-skill-os/fixtures/official/run", headers=headers)
    assert response.status_code == 404
    assert _counts(db_factory) == before
    get_settings.cache_clear()


@pytest.mark.parametrize("name,status", [("official", "succeeded"),
                                          ("rumour", "blocked"),
                                          ("wrong-number", "blocked")])
def test_editor_can_run_only_offline_fixture_without_publishing(client, db_factory, monkeypatch, name, status):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()
    headers = login(client, "editor@plus.test")
    before = _counts(db_factory)
    response = client.post(f"/api/news-skill-os/fixtures/{name}/run", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"]["status"] == status
    assert payload["publication_allowed"] is False
    assert payload["human_approval_required"] is True
    assert _counts(db_factory) == before
    get_settings.cache_clear()


def test_viewer_cannot_run_and_unknown_fixture_is_rejected(client, monkeypatch):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()
    viewer = login(client, "viewer@plus.test")
    editor = login(client, "editor@plus.test")
    assert client.post("/api/news-skill-os/fixtures/official/run", headers=viewer).status_code == 403
    assert client.post("/api/news-skill-os/fixtures/../run", headers=editor).status_code != 200
    assert client.post("/api/news-skill-os/fixtures/missing/run", headers=editor).status_code == 404
    get_settings.cache_clear()
