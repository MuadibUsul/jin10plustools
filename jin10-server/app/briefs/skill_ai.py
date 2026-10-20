"""Optional AI polish for the five fixed-brief text Skills.

The production rule title/body remain the factual source.  The model may only
rewrite wording; every number in its answer must be exactly the set of numbers
in the rule copy.  On any failure the caller keeps the complete production
rule copy, so a model outage can never blank or shorten a brief.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings
from app.services.team_settings import briefs_ai

from .engines.liquidity.copy_tracks import parse_answer

SKILL_NAMES = {
    "volume": "jin10-plus-volume-text",
    "turnover": "jin10-plus-turnover-text",
    "valuation": "jin10-plus-valuation-text",
    "liquidity": "jin10-plus-liquidity-text",
    "oil_etf": "jin10-plus-oil-etf-text",
}
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DAILY = re.compile(r"(?m)^### (\d{4}-\d{2}-\d{2})\s*$")
_BANNED = ("点击", "查看完整", "扫码", "扫描", "关注我们", "投资建议", "仅供参考", "布局机会", "下载APP", "http://", "https://")


def _numbers(text: str) -> set[str]:
    """Compare magnitudes while allowing 减少-7.58% to become 下降7.58%."""
    return {match.group(0).replace(",", "").lstrip("+-") for match in _NUMBER.finditer(text)}


def _asset_dir(category: str) -> Path:
    name = SKILL_NAMES.get(category)
    if not name:
        raise ValueError("该栏目没有固定快讯文字 Skill")
    for base in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[3]):
        path = base / "skills" / name
        if path.is_dir():
            return path
    raise ValueError("固定快讯 Skill 规范文件未安装")


def _history(category: str, limit: int = 6) -> str:
    text = (_asset_dir(category) / "references" / "history.md").read_text(encoding="utf-8")
    starts = [match.start() for match in _DAILY.finditer(text)]
    if not starts:
        return ""
    entries = [text[starts[index]:starts[index + 1]] for index in range(len(starts) - 1)]
    entries.append(text[starts[-1]:])
    return "".join(entries[-limit:])


def validate_answer(source_title: str, source_body: str, answer: dict) -> tuple[str, str]:
    if not isinstance(answer, dict) or set(answer) != {"title", "body"}:
        raise ValueError("AI 结果必须只包含 title/body")
    title, body = answer.get("title"), answer.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError("AI 标题或正文类型错误")
    title, body = title.strip(), body.strip()
    if not title or not body or "\n" in title or len(title) > 300 or len(body) > 20000:
        raise ValueError("AI 标题或正文为空、过长或标题不是单行")
    if _numbers(title + "\n" + body) != _numbers(source_title + "\n" + source_body):
        raise ValueError("AI 结果新增、删除或改动了数字")
    if "注：" in body or "--------" in body:
        raise ValueError("AI 结果不应包含固定注释")
    if any(term in title + body for term in _BANNED):
        raise ValueError("AI 结果包含未授权的导流或投资建议")
    return title, body


def polish(team, category: str, source_title: str, source_body: str) -> dict:
    """Return an AI-polished title/body or a sanitized non-ok status."""
    if not get_settings().briefs_skill_ai_enabled:
        return {"status": "disabled", "reason": "disabled"}
    try:
        ai = briefs_ai(team)
    except Exception:
        return {"status": "failed", "reason": "settings_error"}
    if not ai.get("key") or not ai.get("model") or not ai.get("base_url"):
        return {"status": "not_configured", "reason": "provider_not_configured"}
    warnings = []
    if ai.get("provider") == "chat2api" and ai.get("model_supported") is False:
        warnings.append(f"chat2api 不支持 {ai.get('requested_model')}，本次使用 {ai.get('model')}")
    try:
        skill_md = (_asset_dir(category) / "SKILL.md").read_text(encoding="utf-8")
        examples = _history(category)
    except (OSError, ValueError):
        return {"status": "failed", "reason": "skill_asset_missing"}
    system = (
        "你是金十 PLUS 固定快讯编辑。严格遵守下面的 Skill 编辑规范：\n"
        f"{skill_md}\n\n"
        "历史文段只用于语气和结构参考，不得把其中日期、数值或结论当作当日事实。\n"
        "用户给出的是已经完成事实校验的规则稿。只能改写表达、句序和衔接；"
        "必须逐字保留所有数字、日期、百分比、单位、方向和事实归属；"
        "不得新增事件、原因、预测、来源、行动号召、导流语或投资建议；"
        "不得增加“点击查看、关注、布局、机会”等原文没有的句子；不得把固定解释写进正文。"
        "如果只能在原句结构内做轻微润色，就做轻微润色，不要扩写。"
        "标题保持单行。只输出 JSON：{\"title\":\"...\",\"body\":\"...\"}。"
    )
    user = (
        f"栏目：{category}\n\n"
        f"风格参考（只学写法）：\n{examples}\n\n"
        f"已校验规则稿标题：{source_title}\n"
        f"已校验规则稿正文：{source_body}\n\n"
        "请在不改变任何数字和事实的前提下，把文案润色得更清楚、有信息层级，"
        "不要为了文采删减关键事实。"
    )
    payload = {
        "model": ai["model"], "stream": False, "max_tokens": 1200, "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if ai.get("provider") == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    diagnostics = {"provider": ai.get("provider"), "model": ai.get("model"), "warnings": warnings}
    try:
        request = Request(
            ai["base_url"].rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + ai["key"], "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=60) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            return {**diagnostics, "status": "failed", "reason": "response_too_large"}
        data = json.loads(raw)
        choice = data["choices"][0]
        diagnostics["finish_reason"] = choice.get("finish_reason")
        diagnostics["usage"] = {key: value for key, value in (data.get("usage") or {}).items()
                                if key in ("prompt_tokens", "completion_tokens", "total_tokens")
                                and isinstance(value, int)}
        if choice.get("finish_reason") != "stop":
            return {**diagnostics, "status": "failed", "reason": "incomplete_completion"
                    if choice.get("finish_reason") != "length" else "output_truncated"}
        content = choice.get("message", {}).get("content")
        if not content:
            return {**diagnostics, "status": "failed", "reason": "empty_content"}
        title, body = validate_answer(source_title, source_body, parse_answer(content))
        return {**diagnostics, "status": "ok", "title": title, "body": body}
    except HTTPError as exc:
        return {**diagnostics, "status": "failed", "reason": "provider_http_error", "http_status": exc.code}
    except (TimeoutError, URLError, OSError):
        return {**diagnostics, "status": "failed", "reason": "network_error"}
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        return {**diagnostics, "status": "failed", "reason": "invalid_response"}
