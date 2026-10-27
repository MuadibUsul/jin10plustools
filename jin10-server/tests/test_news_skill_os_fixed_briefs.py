"""Fixed brief text assets are registered without using historical numbers as facts."""

import hashlib
from pathlib import Path

import pytest

from app.config import get_settings
from app.news_skill_os.manifest import ManifestError, bundled_registry
from tests.conftest import login


DAY = "2026-10-20"
PREVIOUS = "2026-10-19"
ROOT = Path(__file__).resolve().parents[2]
IDS = {"volume": "news.fixed-volume", "turnover": "news.fixed-turnover",
       "valuation": "news.fixed-valuation", "liquidity": "news.fixed-liquidity",
       "oil_etf": "news.fixed-oil-etf"}


def fact(value, unit, name, *, date=DAY, **other):
    return {"value": value, "unit": unit, "evidence_id": f"source-{name}",
            "as_of": date, "verified": True, **other}


CASES = {
    "volume": {"amount": fact("16800", "yi_cny", "amount"),
               "previous_amount": fact("15400", "yi_cny", "prior", date=PREVIOUS),
               "mean20": fact("17000", "yi_cny", "mean")},
    "turnover": {"low_ma9": fact("71.62", "percent", "low"),
                 "high_ma9": fact("13.87", "percent", "high"),
                 "previous_low_ma9": fact("71.42", "percent", "prior-low", date=PREVIOUS),
                 "previous_high_ma9": fact("14.34", "percent", "prior-high", date=PREVIOUS)},
    "valuation": {"buffett": fact("85.74", "percent", "buffett",
                                   basis="all_a_total_market_cap_over_previous_year_gdp"),
                  "erp": fact("6.01", "percent", "erp"),
                  "previous_erp": fact("6.04", "percent", "prior-erp", date=PREVIOUS)},
    "liquidity": {"dr007_weighted": fact("1.3212", "percent", "dr007"),
                  "previous_dr007_weighted": fact("1.3600", "percent", "prior-dr007", date=PREVIOUS),
                  "policy_7d": fact("1.4000", "percent", "policy")},
    "oil_etf": {"wti_uso": fact("1933.20", "wan_barrels", "wti"),
                "brent_bno": fact("691.40", "wan_barrels", "brent")},
}


@pytest.mark.parametrize("category", IDS)
def test_five_registered_skills_use_only_current_evidence_and_team_note(category):
    skill = bundled_registry().get(IDS[category], "0.1.0")
    note = "注：团队定制的固定解释。" if category != "oil_etf" else "点击查看团队持仓数据。"
    result = skill.run({"data_date": DAY, "facts": CASES[category], "fixed_note": note})
    assert result["title"] and result["dynamic_body"].startswith("2026年10月20日")
    assert result["fixed_note"] == note
    assert note not in result["dynamic_body"]
    assert result["publication_allowed"] is False
    assert set(result["evidence_ids"]) == {f["evidence_id"] for f in CASES[category].values()}
    if category in ("turnover", "valuation", "liquidity"):
        assert note not in result["body"]
        assert result["notes_placement"] == "image"
    else:
        assert note in result["body"]
    for asset in skill.manifest["editorial_assets"]:
        assert hashlib.sha256((ROOT / asset["path"]).read_bytes()).hexdigest() == asset["sha256"]


def test_old_valuation_basis_and_missing_verification_are_rejected():
    from copy import deepcopy
    skill = bundled_registry().get(IDS["valuation"], "0.1.0")
    payload = {"data_date": DAY, "facts": deepcopy(CASES["valuation"]), "fixed_note": ""}
    payload["facts"]["buffett"]["basis"] = "csi300_market_cap_over_gdp"
    with pytest.raises(ValueError, match="口径未核实"):
        skill.run(payload)
    payload["facts"]["buffett"]["basis"] = "all_a_total_market_cap_over_previous_year_gdp"
    payload["facts"]["erp"]["verified"] = False
    with pytest.raises(ManifestError, match="input"):
        skill.run(payload)


@pytest.mark.parametrize("category", IDS)
def test_stale_current_or_forged_prior_dates_fail_closed(category):
    from copy import deepcopy
    facts = deepcopy(CASES[category])
    key = next(iter(facts))
    facts[key]["as_of"] = "2026-10-17"
    with pytest.raises(ValueError, match="data date"):
        bundled_registry().get(IDS[category], "0.1.0").run(
            {"data_date": DAY, "facts": facts, "fixed_note": ""})


def test_liquidity_without_policy_does_not_claim_current_tightness():
    facts = {"dr007_weighted": CASES["liquidity"]["dr007_weighted"]}
    result = bundled_registry().get(IDS["liquidity"], "0.1.0").run(
        {"data_date": DAY, "facts": facts, "fixed_note": ""})
    assert "宽松" not in result["dynamic_body"] and "偏紧" not in result["dynamic_body"]


def test_fixed_brief_preview_is_flagged_editor_only_and_never_publishes(client, db_factory, monkeypatch):
    monkeypatch.setenv("NEWS_SKILL_OS_ENABLED", "true")
    get_settings.cache_clear()
    editor = login(client, "editor@plus.test")
    viewer = login(client, "viewer@plus.test")
    payload = {"data_date": DAY, "facts": CASES["volume"]}
    assert client.post("/api/news-skill-os/fixed-briefs/volume/preview", json=payload,
                       headers=viewer).status_code == 403
    response = client.post("/api/news-skill-os/fixed-briefs/volume/preview", json=payload,
                           headers=editor)
    assert response.status_code == 200, response.text
    assert response.json()["publication_allowed"] is False
    assert "注：" in response.json()["result"]["fixed_note"]
    payload["fixed_note"] = "注：绕开团队配置。"
    assert client.post("/api/news-skill-os/fixed-briefs/volume/preview", json=payload,
                       headers=editor).status_code == 422
    get_settings.cache_clear()
