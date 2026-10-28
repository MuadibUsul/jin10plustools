"""Three-part Plus annotation for one financial news flash.

The model receives only the submitted flash plus a small public-news search
context. It must return plain text with 前因 / 后果 / 补充说明; the editor reviews
and saves the annotation as a draft. No auto publication.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import httpx
from lxml import etree

from app.services.team_settings import briefs_ai
from .editorial_quality import prompt_for, review_text


class AnnotationAIError(ValueError):
    pass


SYSTEM = """你是一名实时财经新闻背景挖掘与因果分析助手。针对用户输入的单条财经快讯，生成三段有实质增量的中文注解，固定为“前因 / 后果 / 补充说明”。目标读者是交易员、投资者和金融新闻关注者。

只分析当前这一条快讯，不得混入其他快讯的主体、事实、数字、时间、地点或结论。原文之外的信息才是注解价值，不要换一种说法重复原文。涉及最新政策、市场数据、公司公告、讲话、战争外交、产业投资、订单、监管、价格、产能、库存、供应量或历史数字时，先核实再分析；无法核实时简短说明边界，不编造。事实、估算、传闻和推断必须区分。结论前置，每段第一句直接回答该段核心问题。

三段职责：
前因：为什么现在发生，补充最近事件链、直接触发因素、此前政策/公司动作、供需、资本开支、订单、监管、制裁、利率或价格变化。
后果：接下来最先影响什么，沿最短直接传导链分析供应、需求、库存、价格、收入、利润、订单、产能、现金流、产业链、政策或谈判；区分已发生影响和潜在影响。
补充说明：只选一个最有价值、容易忽略的连接点（历史对比、同口径数字、产业链环节、政策矛盾、统计口径或关键指标），不要写成杂项清单或复述前两段。

通常每段2—4句，三段合计约300—450个汉字；简单新闻可更短，复杂新闻总长度不超过600字。不要为了凑字数重复或推测。每句15—35字为宜，超过45字拆分。每句话都要增加新事实、机制、比较或判断。

使用普通文本，固定格式：
前因：

正文。

后果：

正文。

补充说明：

正文。

三段之间空一行；不要输出JSON、YAML、代码围栏、表格、列表或来源清单。来源自然融入正文，不展示内部检索过程。避免“总体来看”“值得注意的是”“这意味着”“对投资者而言”“后续需关注”“建议关注”“不是……而是……”等空话；不要先立错误观点再反驳，不要堆叠“如果……那么……”。信息不足时宁可简短。"""


def _search_context(text: str) -> tuple[list[dict], list[str]]:
    """Best-effort public news search; failures never block the annotation."""
    query = re.sub(r"\s+", " ", text).strip()[:120]
    if not query:
        return [], []
    url = ("https://news.google.com/rss/search?q=" + quote(query) +
           "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans")
    try:
        with httpx.Client(timeout=10, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (compatible; PLUS-NewsSkill/1.0)"}) as client:
            response = client.get(url)
            response.raise_for_status()
        root = etree.fromstring(response.content)
        items = []
        for node in root.xpath("//item")[:8]:
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            published = (node.findtext("pubDate") or "").strip()
            if title and link:
                items.append({"title": title[:300], "link": link[:2048], "published": published[:100]})
        return items, []
    except Exception:
        return [], ["公开新闻检索暂不可用，注解仅基于原文；请人工核对最新事实。"]


def analyze(team, flash: str, source_url: str = "") -> dict:
    ai = briefs_ai(team)
    if not ai.get("key") or not ai.get("model") or not ai.get("base_url"):
        raise AnnotationAIError("AI 未配置，无法生成注解")
    search, warnings = _search_context(flash)
    context = json.dumps({
        "flash": flash, "source_url": source_url, "public_search": search,
        "now_beijing": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
    }, ensure_ascii=False)
    payload = {
        "model": ai["model"], "stream": False, "max_tokens": 2200, "temperature": 0.2,
        "messages": [{"role": "system", "content": SYSTEM + prompt_for(team)},
                     {"role": "user", "content": context}],
    }
    if ai.get("provider") == "deepseek":
        payload["thinking"] = {"type": "disabled"}
    try:
        request = Request(ai["base_url"].rstrip("/") + "/chat/completions",
                          data=json.dumps(payload).encode("utf-8"),
                          headers={"Authorization": "Bearer " + ai["key"],
                                   "Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=90) as response:
            raw = response.read(131073)
        if len(raw) > 131072:
            raise AnnotationAIError("AI 注解结果过大")
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop" or not choice.get("message", {}).get("content"):
            raise AnnotationAIError("AI 注解未完成，请重试")
        answer = choice["message"]["content"].strip()
    except HTTPError as exc:
        if exc.code in (402, 429):
            raise AnnotationAIError("AI 额度不足，暂时无法生成注解") from exc
        raise AnnotationAIError("AI 注解请求失败，请稍后重试") from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise AnnotationAIError("AI 注解超时或网络不可用") from exc
    except AnnotationAIError:
        raise
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise AnnotationAIError("AI 注解结果格式不正确") from exc
    answer = re.sub(r"^```(?:text)?\s*|\s*```$", "", answer).strip()
    match = re.search(r"前因：\s*(.+?)\s*后果：\s*(.+?)\s*补充说明：\s*(.+)$", answer, re.S)
    if not match:
        raise AnnotationAIError("AI 注解没有按前因/后果/补充说明输出")
    sections = {name: body.strip() for name, body in zip(("前因", "后果", "补充说明"), match.groups())}
    if any(not body for body in sections.values()):
        raise AnnotationAIError("AI 注解有空白段落")
    total = len(re.sub(r"\s", "", "".join(sections.values())))
    if total > 600:
        raise AnnotationAIError(f"AI 注解超过600字（当前{total}字）")
    body = "\n\n".join(f"{name}：\n\n{sections[name]}" for name in ("前因", "后果", "补充说明"))
    title = "Plus注解｜" + re.sub(r"\s+", " ", flash).strip()[:50]
    if search:
        warnings.append(f"已参考 {len(search)} 条公开新闻检索摘要；仍需人工核对一手来源。")
    quality = review_text(team, target="annotation", title=title, body=body)
    warnings.extend(quality["issues"])
    return {"title": title, "body": body, "sections": sections,
            "sources": [item["link"] for item in search], "warnings": warnings,
            "quality_review": quality,
            "char_count": total, "publication_allowed": False,
            "human_approval_required": True}
