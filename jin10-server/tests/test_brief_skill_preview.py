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
def test_retired_preview_never_calls_ai(client, issues, category, monkeypatch):
    monkeypatch.setattr('app.briefs.skill_ai.polish',lambda *args:pytest.fail('retired GET must not call AI'))
    for email in ('editor@plus.test','viewer@plus.test'):
        response=client.get(f'/api/briefs/issues/{issues[category]}/skill-preview',
            headers=login(client,email),params={'version':1})
        assert response.status_code==410
    assert client.get(f'/api/briefs/issues/{issues[category]}/skill-preview',
        headers=login(client,'out@plus.test'),params={'version':1}).status_code==404
