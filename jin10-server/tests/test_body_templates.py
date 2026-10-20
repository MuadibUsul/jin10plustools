"""Team body templates can replace built-in copy without bypassing facts."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.briefs.body_templates import TemplateError, apply, render
from tests.conftest import login


PAYLOAD = {
    "data_date": "2026-10-20",
    "title": "【A股温度计】单位测试",
    "dynamic_body": "内置正文",
    "facts": {"previous_date": "2026-10-19", "amount_yi": "14600", "amount_wan_yi": "1.46",
              "change_yi": "186.00", "change_pct": "+1.29", "streak": 7,
              "side": "低于", "ma20_yi": "16900", "ma20_wan_yi": "1.69", "activity": "回升"},
}


def test_custom_volume_template_renders_only_named_values():
    body = render("volume", "较前一交易日{change_yi}亿元；连续{streak}个交易日{side}20日均线。", PAYLOAD)
    assert body == "较前一交易日186.00亿元；连续7个交易日低于20日均线。"
    with pytest.raises(TemplateError, match="格式|占位符"):
        render("volume", "{amount.__class__}", PAYLOAD)
    with pytest.raises(TemplateError, match="缺少"):
        render("volume", "{not_found}", PAYLOAD)


def test_toggle_off_uses_team_template_and_on_keeps_builtin():
    payload = dict(PAYLOAD)
    apply(payload, "volume", {"brief_templates": {"volume": {"use_builtin": False,
          "template": "团队正文{streak}个交易日"}}})
    assert payload["dynamic_body"] == "团队正文7个交易日" and payload["body_template_custom"] is True
    builtin = dict(PAYLOAD)
    apply(builtin, "volume", {"brief_templates": {"volume": {"use_builtin": True, "template": "ignored"}}})
    assert builtin["dynamic_body"] == "内置正文"


def test_settings_save_and_validate_team_body_template(client):
    admin = login(client, "admin@plus.test")
    current = client.get("/api/settings", headers=admin).json()
    response = client.put("/api/settings", headers=admin, json={
        "revision": current["revision"],
        "brief_templates": {"volume": {"use_builtin": False,
            "template": "较前一交易日{change_yi}亿元；连续{streak}个交易日{side}20日均线。"}},
    })
    assert response.status_code == 200, response.text
    saved = response.json()["brief_templates"]["volume"]
    assert saved["use_builtin"] is False and "{streak}" in saved["template"]
    invalid = client.put("/api/settings", headers=admin, json={
        "revision": response.json()["revision"],
        "brief_templates": {"volume": {"use_builtin": False, "template": ""}},
    })
    assert invalid.status_code == 422
    editor = login(client, "editor@plus.test")
    allowed = client.put("/api/settings", headers=editor, json={
        "revision": response.json()["revision"],
        "brief_templates": {"volume": {"use_builtin": True, "template": ""}},
    })
    assert allowed.status_code == 200


def test_turnover_derived_placeholders_validate_without_live_facts():
    from app.briefs.body_templates import validate
    validate("turnover", "{date} {low_direction}{low_ma9}% {high_direction}{high_ma9}% {threshold_side}17%")


def test_valuation_percentages_are_rounded_for_display():
    payload = {"facts": {
        "buffett_percent": "85.90310256373557917623418284",
        "erp_latest": {"erp_percent": "6.062829407236335642802155504"},
        "erp_previous": {"erp_percent": "6.013135249042145593869731801"},
    }}
    assert render("valuation", "{buffett}% {erp}% {previous_erp}%", payload) == "85.90% 6.06% 6.01%"
