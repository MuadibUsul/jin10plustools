"""The five production briefs expose read-only text-Skill previews."""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.db.models import BriefIssue, BriefVersion, NewsEvent
from tests.conftest import login


def payloads():
    return {
        "volume": {
            "data_date": "2026-10-19",
            "facts": {"amount_yi": "14375.12149347", "ma20_yi": "16976.7162619385"},
            "fixed_notes": "成交额注释",
        },
        "turnover": {
            "data_date": "2026-10-19",
            "facts": {"low_ma9": "71.62", "high_ma9": "13.87",
                      "previous_low_ma9": "72.09", "previous_high_ma9": "13.68",
                      "previous_date": "2026-10-16"},
            "fixed_notes": "换手率注释",
        },
        "valuation": {
            "data_date": "2026-10-19",
            "facts": {"buffett_percent": "85.78379455181436486315866063",
                      "erp_latest": {"date": "2026-10-19", "erp_percent": "6.013135249042145593869731801"},
                      "erp_previous": {"date": "2026-10-16", "erp_percent": "6.044307692307692307692307692"}},
            "fixed_notes": "估值注释",
        },
        "liquidity": {
            "data_date": "2026-10-20",
            "facts": {"quotes": {"quote_date": "2026-10-20", "rows": [
                {"product": "DR007", "weighted_rate": "1.3149"},
                {"product": "DR001", "weighted_rate": "1.2595"},
            ]}},
            "fixed_notes": "流动性注释",
        },
        "oil_etf": {
            "data_date": "2026-10-19",
            "facts": {"funds": [{"ticker": "USO", "barrels": "19331983.59"},
                                {"ticker": "BNO", "barrels": "7914000.0"}],
                      "previous_source": {"USO": {"barrels": "20147984.89"},
                                          "BNO": {"barrels": "7045000.0"}},
                      "previous_date": "2026-10-16"},
            "fixed_notes": "原油注释",
        },
    }


@pytest.fixture
def issues(db_factory, seed):
    editor = seed["editor"]
    with db_factory() as db:
        created = {}
        for category, payload in payloads().items():
            issue = BriefIssue(team_id=editor.team_id, issue_date=date(2026, 10, 20),
                               category=category, version=1, confirmed_version=1)
            db.add(issue)
            db.flush()
            db.add(BriefVersion(issue_id=issue.id, number=1, title="原标题", dynamic_body="原正文",
                                payload={"valid": True, **payload}, created_by=editor.id))
            created[category] = str(issue.id)
        db.commit()
        return created


@pytest.mark.parametrize("category", ["volume", "turnover", "valuation", "liquidity", "oil_etf"])
def test_five_skill_previews_are_read_only_and_traceable(client, db_factory, issues, category):
    headers = login(client, "editor@plus.test")
    before = None
    with db_factory() as db:
        before = (db.scalar(select(func.count()).select_from(BriefVersion)),
                  db.scalar(select(func.count()).select_from(NewsEvent)))
    response = client.get(f"/api/briefs/issues/{issues[category]}/skill-preview",
                          headers=headers, params={"version": 1})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["category"] == category
    expected_skill = "news.fixed-oil-etf" if category == "oil_etf" else f"news.fixed-{category}"
    assert data["skill_id"] == expected_skill
    assert data["skill_version"] == "0.1.0"
    assert data["publication_allowed"] is False and data["human_approval_required"] is True
    assert data["title"] and data["dynamic_body"] and data["fixed_note"]
    assert data["evidence_ids"] and all(item.startswith("brief:") for item in data["evidence_ids"])
    with db_factory() as db:
        assert (db.scalar(select(func.count()).select_from(BriefVersion)),
                db.scalar(select(func.count()).select_from(NewsEvent))) == before


def test_skill_preview_permissions_and_team_scope(client, db_factory, issues, seed):
    editor = login(client, "editor@plus.test")
    viewer = login(client, "viewer@plus.test")
    outsider = login(client, "out@plus.test")
    url = f"/api/briefs/issues/{issues['volume']}/skill-preview"
    assert client.get(url, headers=editor, params={"version": 99}).status_code == 404
    assert client.get(url, headers=outsider, params={"version": 1}).status_code == 404
    assert client.get(url, headers=viewer, params={"version": 1}).status_code == 200
    assert client.get(url, headers=viewer).status_code == 422
