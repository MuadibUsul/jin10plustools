"""Team-editable dynamic-body templates and an opt-out from built-in copy.

The facts, charts and source snapshots still come from the validated adapter.
Only the text body can be replaced.  Templates are intentionally limited to
named values; arbitrary Python expressions or attribute access are rejected.
"""
from __future__ import annotations

import string
from typing import Any

CATEGORIES = ("volume", "turnover", "valuation", "liquidity", "oil_etf")
MAX_TEMPLATE = 4000


def _config(team, category: str) -> dict:
    saved = ((getattr(team, "settings", None) or {}).get("brief_templates") or {}).get(category) or {}
    return {
        "use_builtin": saved.get("use_builtin", True) is not False,
        "template": str(saved.get("template") or "")[:MAX_TEMPLATE],
    }


def all_config(team) -> dict[str, dict]:
    return {category: _config(team, category) for category in CATEGORIES}


def _values(category: str, payload: dict) -> dict[str, Any]:
    facts = payload.get("facts") or {}
    values: dict[str, Any] = {
        "date": payload.get("data_date", ""),
        "data_date": payload.get("data_date", ""),
        "previous_date": facts.get("previous_date", ""),
        "title": payload.get("title", ""),
        "builtin_body": payload.get("dynamic_body", ""),
    }
    for key, value in facts.items():
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            values[key] = value
    if category == "volume":
        values.update({
            "amount_yi": facts.get("amount_yi", ""),
            "amount_wan_yi": facts.get("amount_wan_yi", ""),
            "ma20_yi": facts.get("ma20_yi", ""),
            "ma20_wan_yi": facts.get("ma20_wan_yi", ""),
            "change_yi": facts.get("change_yi", ""),
            "change_pct": facts.get("change_pct", ""),
            "streak": facts.get("streak", ""),
            "side": facts.get("side", ""),
            "activity": facts.get("activity", ""),
        })
    elif category == "turnover":
        values.update({
            "low_ma9": facts.get("low_ma9", ""), "high_ma9": facts.get("high_ma9", ""),
            "previous_low_ma9": facts.get("previous_low_ma9", ""),
            "previous_high_ma9": facts.get("previous_high_ma9", ""),
            "low_change_pp": facts.get("low_change_pp", ""), "high_change_pp": facts.get("high_change_pp", ""),
            "low_threshold": facts.get("low_threshold", ""), "high_threshold": facts.get("high_threshold", ""),
        })
        try:
            low, old_low = float(facts.get("low_ma9")), float(facts.get("previous_low_ma9"))
            high, old_high = float(facts.get("high_ma9")), float(facts.get("previous_high_ma9"))
            values["low_direction"] = "降至" if low < old_low else "升至" if low > old_low else "维持"
            values["high_direction"] = "降至" if high < old_high else "升至" if high > old_high else "维持"
            values["threshold_side"] = "低于" if high < 17 else "高于"
        except (TypeError, ValueError):
            pass
    elif category == "valuation":
        latest = facts.get("erp_latest") or {}
        previous = facts.get("erp_previous") or {}
        values.update({"buffett": facts.get("buffett_percent", ""), "erp": latest.get("erp_percent", ""),
                       "previous_erp": previous.get("erp_percent", ""),
                       "erp_date": latest.get("date", ""), "erp_previous_date": previous.get("date", "")})
    elif category == "liquidity":
        quotes = facts.get("quotes") or {}
        rows = {row.get("product"): row for row in quotes.get("rows") or [] if isinstance(row, dict)}
        values.update({"dr007": (rows.get("DR007") or {}).get("weighted_rate", ""),
                       "dr001": (rows.get("DR001") or {}).get("weighted_rate", ""),
                       "quote_date": quotes.get("quote_date", ""), "quote_time": quotes.get("updated_at", "")})
    elif category == "oil_etf":
        funds = {fund.get("ticker"): fund for fund in facts.get("funds") or [] if isinstance(fund, dict)}
        previous = facts.get("previous_source") or {}
        for ticker, prefix in (("USO", "uso"), ("BNO", "bno")):
            fund = funds.get(ticker) or {}
            try:
                values[f"{prefix}_wan_barrels"] = f"{float(fund.get('barrels'))/10000:.2f}"
            except (TypeError, ValueError):
                values[f"{prefix}_wan_barrels"] = ""
            old = (previous.get(ticker) or {}).get("barrels")
            try:
                values[f"previous_{prefix}_wan_barrels"] = f"{float(old)/10000:.2f}" if old is not None else ""
            except (TypeError, ValueError):
                values[f"previous_{prefix}_wan_barrels"] = ""
            try:
                values[f"{prefix}_change_wan_barrels"] = f"{float(fund.get('change_barrels'))/10000:.2f}"
            except (TypeError, ValueError):
                values[f"{prefix}_change_wan_barrels"] = ""
    return values


class TemplateError(ValueError):
    pass


def validate(category: str, template: str) -> None:
    if category not in CATEGORIES:
        raise TemplateError("该栏目不支持正文模板")
    if len(template) > MAX_TEMPLATE:
        raise TemplateError("正文模板过长")
    formatter = string.Formatter()
    try:
        fields = [field for _, field, _, _ in formatter.parse(template) if field]
    except ValueError as exc:
        raise TemplateError("正文模板格式不正确") from exc
    if any(any(ch in field for ch in ".[]") for field in fields):
        raise TemplateError("正文模板占位符不正确")
    known = set(_values(category, {"facts": {}}).keys())
    unknown = sorted(set(fields) - known)
    if unknown:
        raise TemplateError("模板缺少可用的栏目数据：" + "、".join(unknown))


def render(category: str, template: str, payload: dict) -> str:
    if category not in CATEGORIES:
        raise TemplateError("该栏目不支持正文模板")
    if len(template) > MAX_TEMPLATE:
        raise TemplateError("正文模板过长")
    formatter = string.Formatter()
    try:
        for _, field, _, _ in formatter.parse(template):
            if field is None:
                continue
            if not field or any(ch in field for ch in ".[]"):
                raise TemplateError(f"模板占位符不正确：{field}")
    except ValueError as exc:
        raise TemplateError("正文模板格式不正确") from exc
    values = _values(category, payload)
    missing = sorted({field for _, field, _, _ in formatter.parse(template)
                      if field and field not in values})
    if missing:
        raise TemplateError("模板缺少可用的栏目数据：" + "、".join(missing))
    try:
        return template.format_map(values).strip()
    except (KeyError, ValueError, IndexError) as exc:
        raise TemplateError("正文模板渲染失败") from exc


def apply(payload: dict, category: str, team_settings: dict | None) -> dict:
    config = ((team_settings or {}).get("brief_templates") or {}).get(category) or {}
    if config.get("use_builtin", True) is not False:
        return payload
    template = str(config.get("template") or "").strip()
    if not template:
        payload.setdefault("warnings", []).append("团队正文模板为空，已回退内置文案")
        return payload
    try:
        body = render(category, template, payload)
    except TemplateError as exc:
        payload.setdefault("warnings", []).append(f"团队正文模板无效（{exc}），已回退内置文案")
        return payload
    payload["dynamic_body"] = body
    payload["body_template_custom"] = True
    return payload
