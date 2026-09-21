"""素材分析：用 JEV 回答一组原子问题，在代码中合成栏目与评分。

按 TypeSafe 的建议，复杂判断拆成独立的小问题（栏目、市场影响、新颖程度、参考价值、是否政策），
权重在代码中控制，团队可在 teams.settings["scoring"] 中覆盖默认值。
只读取素材文字用于判断，不改写任何内容。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from app.db.models import Material

# ---------- 问题 ----------

SECTION_CRITERIA = {
    "macro": "宏观消息：央行与利率、通胀、就业、财政、汇率、大宗商品、地缘政治与国际关系",
    "cn_equity": "国内股市动向：A股、港股、国内上市公司、板块与行业政策",
    "global_equity": "海外股市动向：美股、欧股、日韩等海外市场与海外上市公司",
    "ai": "AI热点：人工智能技术、大模型、算力与芯片、AI 产业与应用",
    "research": "精选研报：券商、投行或研究机构发布的报告与观点",
    "charts": "精选图表：以数据图表为核心、围绕一组数据展开的内容",
}

QUESTIONS: dict[str, dict[str, Any]] = {
    "section": {
        "type": "choice",
        "instructions": "这条财经新闻最适合放入日报的哪个栏目？",
        "criteria": SECTION_CRITERIA,
    },
    "impact": {
        "type": "score",
        "instructions": "这条新闻对金融市场的影响范围有多大？",
        "criteria": ["几乎没有市场影响", "影响个别公司或细分板块", "影响一个行业或一类资产", "影响大类资产或全球市场"],
    },
    "novelty": {
        "type": "score",
        "instructions": "这条新闻的信息有多新、多出乎意料？",
        "criteria": ["例行数据或旧闻重提", "常规进展，基本符合预期", "重要新进展或明显超预期", "重大突发事件"],
    },
    "value": {
        "type": "score",
        "instructions": "这条新闻对专业投资者的参考价值有多高？",
        "criteria": ["几乎没有参考价值", "有一定参考价值", "参考价值较高", "必读，可能直接影响投资决策"],
    },
    "policy": {
        "type": "noul",
        "instructions": "这条新闻涉及央行、政府或监管机构的政策决定",
    },
}

SECTION_LABELS = {k: v.split("：")[0] for k, v in SECTION_CRITERIA.items()}


def build_state(m: Material) -> str:
    """把素材整理为 JEV 的 state 文本（原文照录）"""
    lines = [f"标题：{m.title}"]
    if m.flash_title and m.flash_title != m.title:
        lines.append(f"快讯标题：{m.flash_title}")
    if m.tags:
        lines.append(f"标签：{'、'.join(m.tags)}")
    lines.append(f"快讯正文：{m.content}")
    interp = m.interpretation or {}
    for module in interp.get("modules", []):
        heading = module.get("heading") or "PLUS解读"
        lines.append(f"{heading}：{' '.join(module.get('paragraphs', []))}")
    images = len(m.images) + sum(len(mod.get("images", [])) for mod in interp.get("modules", []))
    if images:
        lines.append(f"配图：{images} 张")
    return "\n".join(lines)


# ---------- 评分 ----------


@dataclass(frozen=True)
class ScoringConfig:
    """重要度 = 各问题得分加权；综合分 = 重要度 + 团队热度 + 时效 + 完整度 加权"""

    impact: float = 0.4
    novelty: float = 0.3
    value: float = 0.3
    policy_bonus: float = 5.0

    w_importance: float = 0.6
    w_heat: float = 0.2
    w_timeliness: float = 0.1
    w_completeness: float = 0.1

    level_s: int = 85
    level_a: int = 70
    level_b: int = 50

    #: 栏目置信度低于该值时提示人工确认
    low_confidence: float = 0.5

    @classmethod
    def from_team_settings(cls, settings: dict[str, Any] | None) -> ScoringConfig:
        overrides = (settings or {}).get("scoring", {})
        valid = {k: v for k, v in overrides.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def _normalized(answer: dict[str, Any]) -> float:
    """Score 答案归一化到 0–1（score 为 0..N-1 的期望值）"""
    levels = len(answer.get("legend") or {}) or 4
    return max(0.0, min(1.0, float(answer.get("score", 0.0)) / (levels - 1)))


def importance_score(answers: dict[str, Any], cfg: ScoringConfig) -> int:
    weighted = (
        cfg.impact * _normalized(answers["impact"])
        + cfg.novelty * _normalized(answers["novelty"])
        + cfg.value * _normalized(answers["value"])
    ) / (cfg.impact + cfg.novelty + cfg.value)
    bonus = cfg.policy_bonus * float(answers.get("policy", {}).get("noul", 0.0))
    return round(max(0.0, min(100.0, weighted * 100 + bonus)))


def heat_score(m: Material) -> float:
    """团队热度：1 人收藏 25，4 人及以上 100"""
    return min(len(m.collected_by), 4) / 4 * 100


def timeliness_score(m: Material, now: dt.datetime) -> float:
    ts = m.publish_time or m.collect_time
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.UTC)
    hours = (now - ts).total_seconds() / 3600
    if hours <= 6:
        return 100
    if hours <= 24:
        return 70
    if hours <= 48:
        return 40
    return 10


def completeness_score(m: Material) -> float:
    interp = m.interpretation or {}
    has_interp = m.interpretation_status in ("fetched", "manual")
    has_image = bool(m.images) or any(mod.get("images") for mod in interp.get("modules", []))
    return (60 if has_interp else 0) + (40 if has_image else 0)


def final_score(m: Material, cfg: ScoringConfig, now: dt.datetime) -> int:
    total = (
        cfg.w_importance * m.importance_score
        + cfg.w_heat * heat_score(m)
        + cfg.w_timeliness * timeliness_score(m, now)
        + cfg.w_completeness * completeness_score(m)
    ) / (cfg.w_importance + cfg.w_heat + cfg.w_timeliness + cfg.w_completeness)
    return round(total)


def level_for(score: int, cfg: ScoringConfig) -> str:
    if score >= cfg.level_s:
        return "S"
    if score >= cfg.level_a:
        return "A"
    if score >= cfg.level_b:
        return "B"
    return "C"


def apply_answers(m: Material, answers: dict[str, Any], cfg: ScoringConfig, now: dt.datetime) -> None:
    """把 JEV 答案写入素材：栏目、置信度、重要度、综合分、等级、说明"""
    section = answers["section"]
    if not m.category_locked:
        m.category = section["choice"]
    m.ai_confidence = float(section.get("confidence", 0.0))
    m.importance_score = importance_score(answers, cfg)
    m.final_score = final_score(m, cfg, now)
    m.level = level_for(m.final_score, cfg)
    m.ai_details = answers

    parts = [
        f"栏目 {SECTION_LABELS.get(m.category, m.category)}（置信 {m.ai_confidence:.2f}）",
        f"影响 {answers['impact']['score']:.1f}/3 · 新颖 {answers['novelty']['score']:.1f}/3 · 价值 {answers['value']['score']:.1f}/3",
    ]
    if float(answers.get("policy", {}).get("noul", 0)) >= 0.5:
        parts.append("涉及政策决定")
    if m.ai_confidence < cfg.low_confidence:
        parts.append("栏目置信度低，建议人工确认")
    m.ai_reason = "；".join(parts)


def refresh_final_score(m: Material, cfg: ScoringConfig, now: dt.datetime) -> None:
    """收藏人数等变化后只重算综合分，无需重新调用 JEV"""
    if m.ai_status == "done":
        m.final_score = final_score(m, cfg, now)
        m.level = level_for(m.final_score, cfg)
