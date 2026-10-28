"""Plus新鲜事 link analysis and 400-character draft proposal.

A single explicit editor action may call the team's selected AI.  The result is
only a proposal: claims are stored as unverified, the analysis is shown for
confirmation, and no draft exists until the editor requests it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.briefs.engines.liquidity.copy_tracks import parse_answer
from app.services.team_settings import briefs_ai
from .editorial_quality import prompt_for, review_text


class FreshAIError(ValueError):
    pass


def _normal(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _source_span(source: str, quote: str) -> str:
    """Return the original contiguous source span, allowing whitespace differences."""
    positions = [index for index, char in enumerate(source) if not char.isspace()]
    needle = _normal(quote)
    if not needle:
        return ""
    offset = _normal(source).find(needle)
    if offset < 0:
        return ""
    return source[positions[offset]:positions[offset + len(needle) - 1] + 1]


def _numbers(text: str) -> set[str]:
    return {item.replace(",", "") for item in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _list(value, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(item, 1000) for item in value[:limit] if _text(item, 1000)]


def _repair_evidence(ai: dict, article_text: str, missing: list[dict]) -> dict[str, str]:
    """Ask for literal source spans only when the first response paraphrased them.

    The caller still verifies every returned span against the fetched article.
    An empty or invented quote cannot become stored evidence.
    """
    system = (
        "你是原文定位器，只输出 JSON：{\"quotes\":{\"序号\":\"原文片段\"}}。"
        "根据每条断言，从提供的网页正文中逐字复制一段连续原文作为证据，最多500字。"
        "保留原文的大小写、标点和数字；不要改写、拼接或补充。"
        "如果正文没有直接支持该断言的连续片段，该序号的值填空字符串。"
    )
    user = json.dumps({"article": article_text[:60000], "claims": missing}, ensure_ascii=False)
    payload = {
        "model": ai["model"], "stream": False, "max_tokens": 1800, "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if ai.get("provider") == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    try:
        request = Request(ai["base_url"].rstrip("/") + "/chat/completions",
                          data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + ai["key"],
                                   "Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=90) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            return {}
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            return {}
        answer = parse_answer(choice.get("message", {}).get("content", ""))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError):
        return {}
    quotes = answer.get("quotes") if isinstance(answer, dict) else None
    if not isinstance(quotes, dict):
        return {}
    return {str(key): _text(value, 500) for key, value in quotes.items()}


def analyze(team, article: dict, focus: str = "") -> dict:
    ai = briefs_ai(team)
    if not ai.get("key") or not ai.get("model") or not ai.get("base_url"):
        raise FreshAIError("AI 未配置，无法分析新闻链接")
    source_link = article["url"]
    system = (
        "你是金十 PLUS「新鲜事」资深财经编辑。严格遵守以下规则。\n"
        "格式：标题一行、加粗；正文一段，以电头【地点】开头；正文不超过400字（不要求写满）；"
        "风格接近路透社/彭博社，专业、准确、通俗；正文后另起一行写备注，包含字数、未经核实项目、"
        "目测读数误差等。\n"
        "选题：只聚焦一个主题；以最新、最及时内容为主线；用户指定焦点时按焦点写。\n"
        "忠实原文：不添加原文没有的事实；不编造信源；不夸大因果；单一来源数字要注明；"
        "当事方单方面说法必须写明出处。\n"
        "时间与数字：所有时间换算为北京时间；原文没有具体时间的写日期不写星期；核对“昨天/上周六/即将公布”"
        "等相对时间；所有数字回到原文逐一核对并验算；区分统计口径；保留关键限定条件。"
        "发现不一致或不确定时写入备注/未核实项，不要自行编造。\n"
        "先做深度梳理：时间线、关键数字、来源链接、信源原话和编辑判断分开。\n"
        "只输出 JSON，不要代码块：\n"
        "{\"event_title\":\"...\",\"claims\":[{\"subject\":\"...\",\"predicate\":\"...\","
        "\"text\":\"...\",\"evidence\":\"原文连续片段\"}],"
        "\"analysis\":{\"timeline\":[\"...\"],\"key_numbers\":[\"...\"],"
        "\"source_quotes\":[\"...\"],\"judgment\":[\"...\"],\"unverified\":[\"...\"],"
        "\"warnings\":[\"...\"],\"dateline\":\"【城市】\",\"title\":\"...\","
        "\"body\":\"【城市】...\",\"notes\":\"字数：...；未经核实：...；...\"},\"sources\":[\"...\"]}"
    ) + prompt_for(team)
    user = json.dumps({
        "link": source_link, "page_title": article.get("title", ""),
        "site": article.get("site", ""), "published_at": article.get("published_at", ""),
        "now_beijing": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        "focus": focus.strip(), "article": article["text"][:60000],
    }, ensure_ascii=False)
    payload = {
        "model": ai["model"], "stream": False, "max_tokens": 3000, "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if ai.get("provider") == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    try:
        request = Request(ai["base_url"].rstrip("/") + "/chat/completions",
                          data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + ai["key"],
                                   "Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=90) as response:
            raw = response.read(196609)
        if len(raw) > 196608:
            raise FreshAIError("AI 分析结果过大")
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop" or not choice.get("message", {}).get("content"):
            raise FreshAIError("AI 分析未完成，请重试")
        answer = parse_answer(choice["message"]["content"])
    except HTTPError as exc:
        if exc.code in (402, 429):
            raise FreshAIError("AI 额度不足，暂时无法分析链接") from exc
        raise FreshAIError("AI 分析请求失败，请稍后重试") from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise FreshAIError("AI 分析超时或网络不可用") from exc
    except FreshAIError:
        raise
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise FreshAIError("AI 分析结果格式不正确") from exc

    if not isinstance(answer, dict):
        raise FreshAIError("AI 分析结果格式不正确")
    raw_claims = answer.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise FreshAIError("新闻链接中没有提取到可核验断言")
    article_text = article["text"]
    claims = []
    candidates = []
    missing = []
    for item in raw_claims[:12]:
        if not isinstance(item, dict):
            continue
        subject, predicate, claim_text, evidence = (_text(item.get(key), 500) for key in
                                                    ("subject", "predicate", "text", "evidence"))
        if not (subject and predicate and claim_text and evidence):
            continue
        index = len(candidates)
        candidates.append([subject, predicate, claim_text, evidence])
        if _normal(evidence) not in _normal(article_text):
            missing.append({"index": index, "claim": claim_text, "previous_quote": evidence})
    if missing:
        replacements = _repair_evidence(ai, article_text, missing)
        for item in missing:
            index = item["index"]
            replacement = replacements.get(str(index), "")
            source_quote = _source_span(article_text, replacement)
            if source_quote:
                candidates[index][3] = source_quote
    seen = set()
    for subject, predicate, text, evidence in candidates:
        evidence = _source_span(article_text, evidence)
        if not evidence:
            raise FreshAIError("AI 断言的原文证据在新闻页面中不存在")
        if not _numbers(text) <= _numbers(evidence):
            raise FreshAIError("AI 断言包含原文证据中没有的数字")
        key = (subject, predicate, text)
        if key in seen:
            continue
        seen.add(key)
        claims.append({"id": f"c{len(claims)+1}", "subject": subject,
                       "predicate": predicate, "text": text, "evidence": evidence})
    if not claims:
        raise FreshAIError("新闻链接中没有提取到可核验断言")
    analysis = answer.get("analysis")
    if not isinstance(analysis, dict):
        raise FreshAIError("AI 没有返回分析报告")
    dateline = _text(analysis.get("dateline"), 40)
    title = _text(analysis.get("title"), 300)
    body = _text(analysis.get("body"), 5000)
    notes = _text(analysis.get("notes"), 2000)
    if not title or "\n" in title:
        raise FreshAIError("AI 标题为空或不是单行")
    if not dateline.startswith("【") or not dateline.endswith("】"):
        raise FreshAIError("AI 电头格式不正确")
    if not body.startswith(dateline):
        body = dateline + body
    body_chars = len(re.sub(r"\s", "", body))
    if body_chars > 400:
        raise FreshAIError(f"AI 正文超过400字（当前{body_chars}字）")
    if not notes:
        notes = f"字数：{body_chars}字；未经核实项目见分析报告。"
    elif "字数" not in notes:
        notes = f"字数：{body_chars}字；" + notes
    if not _numbers(title + body) <= _numbers(article_text):
        raise FreshAIError("AI 标题或正文包含原文没有的数字")
    result = {
        "event_title": _text(answer.get("event_title") or title, 300),
        "claims": claims,
        "analysis": {
            "timeline": _list(analysis.get("timeline")),
            "key_numbers": _list(analysis.get("key_numbers")),
            "source_quotes": _list(analysis.get("source_quotes")),
            "judgment": _list(analysis.get("judgment")),
            "unverified": _list(analysis.get("unverified")),
            "warnings": _list(analysis.get("warnings")),
            "dateline": dateline, "title": title, "body": body, "notes": notes,
        },
        "sources": _list(answer.get("sources"), 10) + [source_link],
    }
    quality = review_text(team, target="fresh", title=title, body=body,
                          notes=notes, unverified=result["analysis"]["unverified"])
    result["analysis"]["quality_review"] = quality
    result["analysis"]["warnings"].extend(quality["issues"])
    return result
