"""Adapters for the two AI-compute fixed briefs, backed by imported observations."""
from __future__ import annotations

import os
import uuid
import json
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db.models import AIComputeObservation
from app.db.session import get_session_factory
from .ai_compute_charts import gpu_chart, token_chart
from .progress import record


def _rows(kind: str) -> list[dict]:
    team_id = os.getenv('AI_COMPUTE_TEAM_ID', '').strip()
    if not team_id:
        raise ValueError('缺少 AI_COMPUTE_TEAM_ID，无法读取 Token/GPU 结构化数据')
    with get_session_factory()() as db:
        rows = db.scalars(select(AIComputeObservation).where(
            AIComputeObservation.team_id == uuid.UUID(team_id), AIComputeObservation.kind == kind)
            .order_by(AIComputeObservation.date)).all()
        result = []
        for row in rows:
            result.append({
                'date': row.date.isoformat(), 'token_price': row.token_price,
                'a100': row.a100, 'h100': row.h100, 'b200': row.b200,
                'source': row.source, 'status': row.source_status, 'revision': row.revision,
            })
        return result


def _decimal(value):
    return Decimal(str(value)) if value is not None else None


def _pct(current, previous) -> str | None:
    if current is None or previous in (None, 0):
        return None
    return f"{(Decimal(current) / Decimal(previous) - 1) * 100:+.2f}"


def token_cost(day, out, state, refresh=False):
    rows = _rows('token')
    valid = [row for row in rows if row['token_price'] is not None]
    if not valid:
        raise ValueError('Token 支出价格尚无已导入数据')
    latest = valid[-1]
    manual = os.getenv('MANUAL_GENERATION') == '1'
    if latest['date'] != day and not manual:
        raise ValueError(f"Token 数据尚未更新到 {day}；最新已导入为 {latest['date']}")
    actual_day = latest['date']
    record(out, 3, 'Token 支出价格数据校验通过，正在生成历史图')
    previous = next((row for row in valid if row['date'] == (date.fromisoformat(actual_day) - timedelta(days=7)).isoformat()), None)
    weekly = _pct(latest['token_price'], previous['token_price'] if previous else None)
    recent = [row for row in valid if date.fromisoformat(row['date']) >= date.fromisoformat(actual_day) - timedelta(days=6)]
    low = min((row['token_price'] for row in recent), default=latest['token_price'])
    high = max((row['token_price'] for row in recent), default=latest['token_price'])
    peak = max(valid, key=lambda row: (row['token_price'], row['date']))
    price = _decimal(latest['token_price'])
    title_dir = '上涨' if weekly and weekly.startswith('+') and weekly != '+0.00' else '下跌' if weekly and weekly.startswith('-') and weekly != '-0.00' else '持平'
    title = f"【AI算力CPI】Token支出价格指数{title_dir}" if weekly else "【AI算力CPI】Token支出价格指数更新"
    body = f"截至{actual_day}，Token支出价格指数报{price:.4f}美元/百万Token"
    if weekly:
        body += f"，较七个自然日前{'上涨' if title_dir == '上涨' else '下跌' if title_dir == '下跌' else '持平'}{abs(Decimal(weekly)):.2f}%"
    else:
        body += "，七个自然日前的基准值缺失，本次不报告周变化"
    body += f"；最近7日已收录价格区间为{_decimal(low):.4f}—{_decimal(high):.4f}美元。"
    warnings = []
    if actual_day != day:
        warnings.append(f"使用最新可用数据日 {actual_day}，目标数据日 {day} 尚无更新")
    if not previous:
        warnings.append(f"缺少 {actual_day} 七个自然日前的 Token 基准值，本期未报告周变化")
    (out/'ai-compute-source.json').write_text(json.dumps(valid,ensure_ascii=False,default=str),encoding='utf-8')
    chart = token_chart(valid, out)
    return {
        'title': title, 'dynamic_body': body, 'images': [chart.name], 'data_date': actual_day,
        'warnings': warnings,
        'facts': {
            'token_price': f'{price:.4f}', 'previous_date': previous['date'] if previous else '',
            'previous_price': f"{_decimal(previous['token_price']):.4f}" if previous else '',
            'weekly_change_pct': weekly or '', 'range_low': f'{_decimal(low):.4f}',
            'range_high': f'{_decimal(high):.4f}', 'peak_price': f"{_decimal(peak['token_price']):.4f}",
            'peak_date': peak['date'], 'source': 'Silicon Data LLM Token Expenditure Index',
        },
        'process_details': {
            'sources': [{'label': 'Silicon Data LLM Token Expenditure Index', 'url': 'https://www.silicondata.com/products/silicon-index/llm-token-expenditure-index'}],
            'inputs': [{'label': 'Token 支出价格', 'value': f'{price:.4f}', 'unit': '美元/百万Token'}],
            'calculations': ['周变化 = 当日价格 ÷ 七个自然日前价格 − 1；基准日缺失时不替代。',
                             '最近7日区间只使用已导入的非空价格；历史极值保留日期。'],
        },
    }


def gpu_rental(day, out, state, refresh=False):
    rows = _rows('gpu')
    complete = [row for row in rows if all(row[key] is not None for key in ('a100', 'h100', 'b200'))]
    if not complete:
        raise ValueError('GPU 租赁价格尚无完整三卡数据')
    latest = complete[-1]
    manual = os.getenv('MANUAL_GENERATION') == '1'
    if latest['date'] != day and not manual:
        raise ValueError(f"GPU 数据尚未更新到 {day} 的完整三卡数据；最新完整日为 {latest['date']}")
    actual_day = latest['date']
    record(out, 3, 'GPU 三卡租赁价格校验通过，正在生成历史图')
    baseline_day = (date.fromisoformat(actual_day) - timedelta(days=7)).isoformat()
    previous = next((row for row in complete if row['date'] == baseline_day), None)
    changes = {key: _pct(latest[key], previous[key]) if previous else None for key in ('a100', 'h100', 'b200')}
    peaks = {}
    warnings = []
    for key in ('a100', 'h100', 'b200'):
        values = [row for row in rows if row[key] is not None and row['date'] < actual_day]
        if not values:
            raise ValueError(f'{key} 缺少历史峰值数据')
        peaks[key] = max(values, key=lambda row: (row[key], row['date']))
    if actual_day != day:
        warnings.append(f"使用最新可用完整三卡日 {actual_day}，目标数据日 {day} 尚无更新")
    if not previous:
        warnings.append(f"缺少 {baseline_day} 的完整三卡基准，本期未报告周变化")
    title = f"【AI景气度指标】B200租赁价格报{_decimal(latest['b200']):.2f}美元/GPU每小时"
    parts = [f"B200租赁价格报{_decimal(latest['b200']):.2f}美元/GPU每小时"]
    for label, key in (('H100', 'h100'), ('A100', 'a100')):
        parts.append(f"{label}报{_decimal(latest[key]):.2f}美元/GPU每小时")
    body = f"截至{actual_day}，" + "；".join(parts)
    if previous:
        body += "。"
        for label, key in (('B200', 'b200'), ('H100', 'h100'), ('A100', 'a100')):
            change = changes[key]
            body += f"{label}较七个自然日前{'上涨' if change and change.startswith('+') and change != '+0.00' else '下跌' if change and change.startswith('-') and change != '-0.00' else '持平'}{abs(Decimal(change)):.2f}%；" if change else f"{label}周变化基准缺失；"
        body = body.rstrip('；') + '。'
    else:
        body += "；周变化基准缺失。"
    peak = peaks['b200']
    if _decimal(latest['b200']) > _decimal(peak['b200']):
        body += f"B200处于已收录数据新高（此前高点{_decimal(peak['b200']):.2f}美元，{peak['date']}）。"
    elif _decimal(latest['b200']) == _decimal(peak['b200']):
        body += f"B200与已收录数据高点{_decimal(peak['b200']):.2f}美元持平（{peak['date']}）。"
    else:
        body += f"B200较已收录数据高点{_decimal(peak['b200']):.2f}美元低{(_decimal(peak['b200'])-_decimal(latest['b200'])) / _decimal(peak['b200']) * 100:.2f}%。"
    (out/'ai-compute-source.json').write_text(json.dumps(rows,ensure_ascii=False,default=str),encoding='utf-8')
    chart = gpu_chart([row for row in rows if row['date']<=actual_day], out)
    return {
        'title': title, 'dynamic_body': body, 'images': [chart.name], 'data_date': actual_day,
        'warnings': warnings,
        'facts': {
            'a100': f"{_decimal(latest['a100']):.2f}", 'h100': f"{_decimal(latest['h100']):.2f}",
            'b200': f"{_decimal(latest['b200']):.2f}", 'previous_date': previous['date'] if previous else '',
            'a100_change_pct': changes['a100'] or '', 'h100_change_pct': changes['h100'] or '',
            'b200_change_pct': changes['b200'] or '', 'b200_peak': f"{_decimal(peaks['b200']['b200']):.2f}",
            'b200_peak_date': peaks['b200']['date'], 'source': 'Silicon Data A100/H100/B200 NeoCloud',
        },
        'process_details': {
            'sources': [{'label': 'Silicon Data GPU Index', 'url': 'https://www.silicondata.com/products/silicon-index'}],
            'inputs': [{'label': 'A100/H100/B200 租赁价格', 'value': f"{_decimal(latest['a100']):.2f}/{_decimal(latest['h100']):.2f}/{_decimal(latest['b200']):.2f}", 'unit': '美元/GPU/小时'}],
            'calculations': ['周变化按七个自然日对齐，不按行号；基准日缺失时不补值。',
                             '仅使用同日三卡完整行；历史峰值按各卡已导入的非空序列计算，等于高点记为并列。'],
        },
    }
