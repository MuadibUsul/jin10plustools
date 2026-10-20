"""Evidence-bound, deterministic text previews for the five fixed briefs.

Historical examples are editorial references only. This module never reads them
as a source of daily dates, values, or market conclusions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.briefs.delivery import complete_body


REQUIRED = {
    "volume": {"amount": "yi_cny"},
    "turnover": {"low_ma9": "percent", "high_ma9": "percent",
                 "previous_low_ma9": "percent", "previous_high_ma9": "percent"},
    "valuation": {"buffett": "percent", "erp": "percent"},
    "liquidity": {"dr007_weighted": "percent"},
    "oil_etf": {"wti_uso": "wan_barrels", "brent_bno": "wan_barrels"},
}
OPTIONAL = {
    "volume": {"previous_amount": "yi_cny", "mean20": "yi_cny"},
    "turnover": {},
    "valuation": {"previous_erp": "percent"},
    "liquidity": {"previous_dr007_weighted": "percent", "dr007_open": "percent",
                  "dr001_weighted": "percent", "policy_7d": "percent"},
    "oil_etf": {"previous_wti_uso": "wan_barrels", "previous_brent_bno": "wan_barrels"},
}
PREVIOUS = {"previous_amount", "previous_low_ma9", "previous_high_ma9", "previous_erp",
            "previous_dr007_weighted", "previous_wti_uso", "previous_brent_bno"}
Q = Decimal("0.01")


def _number(fact: dict, name: str, unit: str, day: date) -> Decimal:
    if not isinstance(fact, dict) or fact.get("verified") is not True:
        raise ValueError(f"{name}: verified source fact required")
    if fact.get("unit") != unit or not isinstance(fact.get("evidence_id"), str) or not fact["evidence_id"].strip():
        raise ValueError(f"{name}: unit or evidence_id missing")
    try:
        as_of = date.fromisoformat(fact["as_of"])
        raw = fact["value"]
        if isinstance(raw, bool) or (isinstance(raw, str) and "%" in raw):
            raise ValueError()
        value = Decimal(str(raw))
    except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
        raise ValueError(f"{name}: invalid numeric value or date") from exc
    if not value.is_finite() or (name in PREVIOUS and as_of >= day) or (name not in PREVIOUS and as_of != day):
        raise ValueError(f"{name}: non-finite value or wrong data date")
    if value < 0 or (unit == "percent" and value > 100):
        raise ValueError(f"{name}: out of range")
    return value


def _fmt(value: Decimal, places: int = 2) -> str:
    q = Decimal(1).scaleb(-places)
    return f"{value.quantize(q, rounding=ROUND_HALF_UP):.{places}f}"


def _direction(current: Decimal, previous: Decimal) -> str:
    return "升至" if current > previous else "降至" if current < previous else "维持在"


def render(category: str, payload: dict) -> dict:
    if category not in REQUIRED:
        raise ValueError("unknown fixed brief category")
    day = date.fromisoformat(payload["data_date"])
    facts = payload["facts"]
    if not isinstance(facts, dict):
        raise ValueError("facts must be an object")
    allowed = REQUIRED[category] | OPTIONAL[category]
    if missing := REQUIRED[category].keys() - facts.keys():
        raise ValueError(f"missing required facts: {sorted(missing)}")
    if extra := facts.keys() - allowed.keys():
        raise ValueError(f"unexpected facts: {sorted(extra)}")
    values = {key: _number(fact, key, allowed[key], day) for key, fact in facts.items()}
    if category == "valuation" and facts["buffett"].get("basis") != "all_a_total_market_cap_over_previous_year_gdp":
        raise ValueError("buffett: A股总市值／上年度全年GDP口径未核实")
    note = payload["fixed_note"]
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError("fixed_note must be team-configured text")
    prefix = f"{day.year}年{day.month}月{day.day}日"
    if category == "volume":
        amount = values["amount"]
        body = f"{prefix}，沪深两市成交额为{_fmt(amount)}亿元"
        title = f"【A股温度计】两市成交额报{_fmt(amount / 10000)}万亿元"
        if "previous_amount" in values:
            change = amount - values["previous_amount"]
            body += f"，较前一有效交易日{'增加' if change > 0 else '减少' if change < 0 else '持平'}"
            if change:
                body += f"{_fmt(abs(change))}亿元"
        if "mean20" in values:
            relation = "高于" if amount > values["mean20"] else "低于" if amount < values["mean20"] else "等于"
            body += f"，{relation}20日均线（{_fmt(values['mean20'])}亿元）"
        body += "。"
    elif category == "turnover":
        low, high = values["low_ma9"], values["high_ma9"]
        previous_low, previous_high = values["previous_low_ma9"], values["previous_high_ma9"]
        body = (f"{prefix}，A股低于3%换手率股票占比的9日均线{_direction(low, previous_low)}{_fmt(low)}%，"
                f"高于5%换手率股票占比的9日均线{_direction(high, previous_high)}{_fmt(high)}%，"
                f"{'高于' if high > 17 else '低于' if high < 17 else '处于'}17%参考阈值。")
        title = f"【A股温度计】高换手率股票占比{_direction(high, previous_high)}{_fmt(high)}%"
    elif category == "valuation":
        erp = values["erp"]
        body = f"{prefix}，巴菲特指数（A股总市值／上年度全年GDP）为{_fmt(values['buffett'])}%；沪深300指数股权风险溢价（ERP）为{_fmt(erp)}%"
        if "previous_erp" in values:
            delta = erp - values["previous_erp"]
            body += f"，较前一有效交易日{'上升' if delta > 0 else '下降' if delta < 0 else '持平'}"
            if delta:
                body += f"{_fmt(abs(delta))}个百分点"
        body += f"，{'高于' if erp > 6 else '低于' if erp < 6 else '处于'}6%参考线。"
        title = f"【A股温度计】沪深300股权风险溢价报{_fmt(erp)}%"
    elif category == "liquidity":
        rate = values["dr007_weighted"]
        body = f"{prefix}，DR007加权利率报{_fmt(rate, 4)}%"
        if "previous_dr007_weighted" in values:
            bp = (rate - values["previous_dr007_weighted"]) * 100
            body += f"，较前一有效交易日{'上行' if bp > 0 else '下行' if bp < 0 else '持平'}"
            if bp:
                body += f"{_fmt(abs(bp))}个基点"
        if "dr007_open" in values:
            body += f"；盘初DR007报{_fmt(values['dr007_open'], 4)}%"
        if "dr001_weighted" in values:
            body += f"，DR001加权利率报{_fmt(values['dr001_weighted'], 4)}%"
        if "policy_7d" in values:
            difference = (rate - values["policy_7d"]) * 100
            body += f"。同期7天逆回购政策利率为{_fmt(values['policy_7d'], 4)}%，DR007加权利率{'高于' if difference > 0 else '低于' if difference < 0 else '等于'}政策利率"
            if difference:
                body += f"{_fmt(abs(difference))}个基点"
        body += "。"
        title = f"【流动性指标】DR007加权利率报{_fmt(rate, 4)}%"
    else:
        wti, brent = values["wti_uso"], values["brent_bno"]
        body = f"{prefix}，WTI（USO）名义敞口约{_fmt(wti)}万桶"
        if "previous_wti_uso" in values:
            delta = wti - values["previous_wti_uso"]
            body += f"，较前一有效结算日{'增加' if delta > 0 else '减少' if delta < 0 else '持平'}"
            if delta:
                body += f"{_fmt(abs(delta))}万桶"
        body += f"；Brent（BNO）期货持仓约{_fmt(brent)}万桶"
        if "previous_brent_bno" in values:
            delta = brent - values["previous_brent_bno"]
            body += f"，较前一有效结算日{'增加' if delta > 0 else '减少' if delta < 0 else '持平'}"
            if delta:
                body += f"{_fmt(abs(delta))}万桶"
        body += "。"
        title = "【原油ETF】美油与布油ETF持仓数据更新"
    return {"title": title, "dynamic_body": body, "fixed_note": note,
            "body": complete_body(category, body, note),
            "notes_placement": "image" if category in ("turnover", "valuation") else "inline" if category == "oil_etf" else "body",
            "evidence_ids": sorted({fact["evidence_id"] for fact in facts.values()}),
            "publication_allowed": False}
