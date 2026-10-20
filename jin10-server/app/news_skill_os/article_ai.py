"""AI-assisted extraction of atomic claims from a pasted news article.

Extraction only proposes facts. Every proposal must quote an exact passage of
the supplied article; the API stores it as unverified and the normal fact-check
stage must establish support before a draft can pass review. No network access
is made unless an editor explicitly submits an article and the team has a
configured AI provider.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.briefs.engines.liquidity.copy_tracks import parse_answer
from app.services.team_settings import briefs_ai


class ArticleExtractionError(ValueError):
    """An article could not be converted into safe claim proposals."""


def _normal(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _numbers(text: str) -> set[str]:
    return {item.replace(",", "") for item in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def extract(team, title: str, article: str, source_name: str, source_kind: str) -> dict:
    ai = briefs_ai(team)
    if not ai.get("key") or not ai.get("model") or not ai.get("base_url"):
        raise ArticleExtractionError("AI 未配置，无法从新闻稿提取断言")
    system = (
        "你是财经新闻事实提取器。只提取新闻稿中明确写出、可单独核验的原子事实；"
        "每条事实必须给出稿件中的原文证据，不得改写数字、日期、主体或方向；"
        "不要把评论、推测、匿名人士观点升级为事实；没有原文证据的断言不要输出。"
        "同时给出三段供编辑审核的注解：前因、后果、补充说明，每段必须引用已提取的 Claim/证据 ID，"
        "不得引入原文没有的数字或事实，并标注 fact（原文事实）或 inference（推断）。"
        "只输出 JSON：{\"event_title\":\"...\",\"claims\":[{\"subject\":\"...\","
        "\"predicate\":\"...\",\"text\":\"...\",\"evidence\":\"原文连续片段\","
        "\"numeric\":null或{\"value\":数字,\"kind\":\"money|ratio|count|price|rate\","
        "\"unit\":\"单位\",\"currency\":\"币种或null\",\"comparison\":\"level|yoy|mom|qoq|change\"}}],"
        "\"analyses\":[{\"section\":\"前因|后果|补充说明\",\"text\":\"...\","
        "\"label\":\"fact|inference\",\"claim_ids\":[\"c1\"],\"evidence_ids\":[\"e1\"]}]}"
    )
    user = json.dumps({
        "title": title, "source_name": source_name, "source_kind": source_kind,
        "article": article[:60000],
    }, ensure_ascii=False)
    payload = {
        "model": ai["model"], "stream": False, "max_tokens": 2400, "temperature": 0.1,
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
        with urlopen(request, timeout=60) as response:
            raw = response.read(131073)
        if len(raw) > 131072:
            raise ArticleExtractionError("AI 提取结果过大")
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop" or not choice.get("message", {}).get("content"):
            raise ArticleExtractionError("AI 提取未完成，请重试")
        answer = parse_answer(choice["message"]["content"])
    except HTTPError as exc:
        if exc.code in (402, 429):
            raise ArticleExtractionError("AI 额度不足，暂时无法提取新闻稿") from exc
        raise ArticleExtractionError("AI 提取请求失败，请稍后重试") from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise ArticleExtractionError("AI 提取超时或网络不可用") from exc
    except ArticleExtractionError:
        raise
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ArticleExtractionError("AI 提取结果格式不正确") from exc

    claims = answer.get("claims") if isinstance(answer, dict) else None
    if not isinstance(claims, list) or not claims:
        raise ArticleExtractionError("新闻稿中没有提取到可核验断言")
    if len(claims) > 12:
        claims = claims[:12]
    normal_article = _normal(article)
    clean = []
    seen = set()
    for index, item in enumerate(claims, start=1):
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        text = str(item.get("text") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if not (subject and predicate and text and evidence):
            continue
        if _normal(evidence) not in normal_article:
            raise ArticleExtractionError(f"第 {index} 条断言的原文证据在新闻稿中不存在")
        if not _numbers(text) <= _numbers(evidence):
            raise ArticleExtractionError(f"第 {index} 条断言包含证据中没有的数字")
        key = (subject, predicate, text)
        if key in seen:
            continue
        seen.add(key)
        clean.append({
            "id": f"c{len(clean)+1}", "subject": subject, "predicate": predicate,
            "text": text, "evidence": evidence, "numeric": item.get("numeric"),
        })
    if not clean:
        raise ArticleExtractionError("新闻稿中没有提取到可核验断言")
    evidence_ids = {f"e{i}" for i in range(1, len(clean) + 1)}
    claim_ids = {item["id"] for item in clean}
    analyses = []
    for index, item in enumerate(answer.get("analyses") or [], start=1):
        if not isinstance(item, dict):
            continue
        section = item.get("section")
        text = str(item.get("text") or "").strip()
        label = item.get("label")
        refs = [str(value) for value in (item.get("claim_ids") or [])]
        ev_refs = [str(value) for value in (item.get("evidence_ids") or [])]
        if section not in ("前因", "后果", "补充说明") or label not in ("fact", "inference") or not text:
            continue
        if not set(refs) <= claim_ids or not set(ev_refs) <= evidence_ids:
            raise ArticleExtractionError(f"第 {index} 段注解引用了不存在的 Claim 或证据")
        if not refs and not ev_refs:
            continue
        analyses.append({"id": f"a{len(analyses)+1}", "section": section, "text": text,
                         "label": label, "claim_ids": refs, "evidence_ids": ev_refs})
    event_title = str(answer.get("event_title") or title).strip()
    if not event_title or len(event_title) > 300:
        event_title = title.strip()
    return {"title": event_title, "claims": clean, "analyses": analyses}
