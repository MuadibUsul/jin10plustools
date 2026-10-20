"""Editor-facing, read-only preview of the five fixed-brief text skills.

The production generator remains the source of the issue, charts and snapshots.
This adapter only maps the selected version's already-validated facts into the
five `news.fixed-*` skills and returns suggested wording.  Nothing is written,
published or used to replace a generated body automatically.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from app.news_skill_os.manifest import ManifestError, bundled_registry

from .delivery import default_notes

FIXED_SKILLS = {
    "volume": "news.fixed-volume",
    "turnover": "news.fixed-turnover",
    "valuation": "news.fixed-valuation",
    "liquidity": "news.fixed-liquidity",
    "oil_etf": "news.fixed-oil-etf",
}


def _evidence(version, key: str) -> str:
    return f"brief:{version.id}:{version.number}:{key}"


def _fact(value, unit: str, as_of: str, version, key: str, **extra) -> dict:
    text = str(value)
    if isinstance(value, Decimal):
        text = format(value, "f")
    fact = {"value": text, "unit": unit, "as_of": as_of, "verified": True,
            "evidence_id": _evidence(version, key)}
    fact.update(extra)
    return fact


def _decimal(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}: 数值无效") from exc
    if not result.is_finite():
        raise ValueError(f"{label}: 数值无效")
    return result


def _date(value, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: 日期无效") from exc


def build_facts(category: str, payload: dict, version) -> tuple[str, dict]:
    data_date = _date(payload.get("data_date"), "data_date")
    facts = payload.get("facts") or {}
    if not isinstance(facts, dict):
        raise ValueError("稿件 facts 不是对象")
    out: dict[str, dict] = {}
    if category == "volume":
        out["amount"] = _fact(_decimal(facts.get("amount_yi"), "成交额"), "yi_cny",
                              data_date, version, "amount")
        if facts.get("ma20_yi") is not None:
            out["mean20"] = _fact(_decimal(facts["ma20_yi"], "20日均线"), "yi_cny",
                                  data_date, version, "mean20")
        if facts.get("previous_amount_yi") is not None:
            previous = _date(payload.get("previous_date") or facts.get("previous_date"), "前值日期")
            out["previous_amount"] = _fact(_decimal(facts["previous_amount_yi"], "前值成交额"),
                                           "yi_cny", previous, version, "previous_amount")
    elif category == "turnover":
        previous = _date(facts.get("previous_date"), "前值日期")
        for key, label, target in (
            ("low_ma9", "低于3%换手率9日均线", "low_ma9"),
            ("high_ma9", "高于5%换手率9日均线", "high_ma9"),
        ):
            out[target] = _fact(_decimal(facts.get(key), label), "percent",
                                data_date, version, target)
        for key, label, target in (
            ("previous_low_ma9", "前值低于3%换手率9日均线", "previous_low_ma9"),
            ("previous_high_ma9", "前值高于5%换手率9日均线", "previous_high_ma9"),
        ):
            out[target] = _fact(_decimal(facts.get(key), label), "percent",
                                previous, version, target)
    elif category == "valuation":
        latest = facts.get("erp_latest") or {}
        previous = facts.get("erp_previous") or {}
        if latest.get("date") and _date(latest["date"], "ERP 日期") != data_date:
            raise ValueError("ERP 数据日期与稿件数据日不一致")
        out["buffett"] = _fact(_decimal(facts.get("buffett_percent"), "巴菲特指数"), "percent",
                               data_date, version, "buffett",
                               basis="all_a_total_market_cap_over_previous_year_gdp")
        out["erp"] = _fact(_decimal(latest.get("erp_percent"), "ERP"), "percent",
                           data_date, version, "erp")
        if previous.get("erp_percent") is not None:
            out["previous_erp"] = _fact(_decimal(previous["erp_percent"], "前值ERP"), "percent",
                                        _date(previous.get("date"), "前值ERP日期"),
                                        version, "previous_erp")
    elif category == "liquidity":
        quotes = (facts.get("quotes") or {})
        rows = quotes.get("rows") or []
        by_product = {row.get("product"): row for row in rows if isinstance(row, dict)}
        quote_date = _date(quotes.get("quote_date") or data_date, "报价日期")
        if quote_date != data_date:
            raise ValueError("回购报价日期与稿件数据日不一致")
        dr007 = by_product.get("DR007") or {}
        out["dr007_weighted"] = _fact(_decimal(dr007.get("weighted_rate"), "DR007加权利率"),
                                      "percent", data_date, version, "dr007_weighted")
        dr001 = by_product.get("DR001") or {}
        if dr001.get("weighted_rate") is not None:
            out["dr001_weighted"] = _fact(_decimal(dr001["weighted_rate"], "DR001加权利率"),
                                          "percent", data_date, version, "dr001_weighted")
    elif category == "oil_etf":
        funds = {fund.get("ticker"): fund for fund in facts.get("funds") or []
                 if isinstance(fund, dict)}
        previous = facts.get("previous_source") or {}
        previous_date = _date(facts.get("previous_date"), "前值日期")
        for ticker, target, label in (("USO", "wti_uso", "WTI（USO）敞口"),
                                      ("BNO", "brent_bno", "Brent（BNO）持仓")):
            fund = funds.get(ticker) or {}
            out[target] = _fact(_decimal(fund.get("barrels"), label) / Decimal(10000),
                                "wan_barrels", data_date, version, target)
            old = (previous.get(ticker) or {}).get("barrels")
            if old is not None:
                out["previous_" + target] = _fact(
                    _decimal(old, label + "前值") / Decimal(10000), "wan_barrels",
                    previous_date, version, "previous_" + target)
    else:
        raise ValueError("该栏目没有固定快讯文字 Skill")
    return data_date, out


def preview(issue, version) -> dict:
    category = issue.category
    if category not in FIXED_SKILLS:
        raise ValueError("该栏目没有固定快讯文字 Skill")
    payload = version.payload or {}
    data_date, facts = build_facts(category, payload, version)
    note = payload.get("fixed_notes")
    if not isinstance(note, str):
        note = default_notes(category)
    registry = bundled_registry()
    skill_id = FIXED_SKILLS[category]
    skill = registry.get(skill_id, "0.1.0")
    try:
        result = skill.run({"data_date": data_date, "facts": facts, "fixed_note": note})
    except ManifestError as exc:
        raise ValueError(str(exc)) from exc
    return {
        **result,
        "category": category,
        "version": version.number,
        "data_date": data_date,
        "skill_id": skill.key[0],
        "skill_version": skill.key[1],
        "source": "current_brief_version",
        "warnings": ["Skill 文案为辅助预览，不替代现有生成稿；采用前请核对数据与固定解释。"],
        "publication_allowed": False,
        "human_approval_required": True,
    }
