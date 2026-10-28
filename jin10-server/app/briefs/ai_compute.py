"""Adapters for the two AI-compute fixed briefs, backed by imported observations."""
from __future__ import annotations

import hashlib
import os
import uuid
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

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
                'public_display': row.public_display, 'manual_display': row.manual_display,
            })
        return result


def _decimal(value):
    return Decimal(str(value)) if value is not None else None


def _pct(current, previous) -> str | None:
    if current is None or previous in (None, 0):
        return None
    return f"{(Decimal(current) / Decimal(previous) - 1) * 100:+.2f}"


def _public_week(row, metric):
    if row.get('status') == 'manual_entry' or not row.get('public_display'):
        return None
    matches = [item for item in (row.get('public_display') or {}).get('readings', [])
               if item.get('metric') == metric and item.get('date') == row['date']]
    if len(matches) != 1:
        raise ValueError('公开页面七日变化快照不完整')
    item = matches[0]
    if Decimal(item['price']) != _decimal(row['token_price' if metric == 'token' else metric]):
        raise ValueError('公开页面价格与已入库数据不一致')
    return item


def _manual_reading(row, metric):
    if row.get('status') != 'manual_entry':
        return None
    matches = [item for item in (row.get('manual_display') or {}).get('readings', [])
               if item.get('metric') == metric and item.get('date') == row['date']]
    if len(matches) != 1:
        raise ValueError('人工录入来源快照不完整')
    item = matches[0]
    if Decimal(item['price']) != _decimal(row['token_price' if metric == 'token' else metric]):
        raise ValueError('人工录入价格与已入库数据不一致')
    return item


def _direction(change):
    value = Decimal(change)
    return '上涨' if value > 0 else '下跌' if value < 0 else '持平'


def published_notes(category):
    """Notes the worker will store on the edition, known before the chart is drawn."""
    from .delivery import default_notes
    supplied = os.getenv('CATEGORY_NOTES')
    if supplied is None or not supplied.strip():
        return default_notes(category)
    return str(supplied).strip()


def align_chart_notes(payload, category, out, notes):
    """Redraw the chart when the published notes differ from the footer already drawn."""
    notes = '' if notes is None else str(notes).strip()
    if payload.get('chart_notes') == notes and payload.get('fixed_notes') == notes:
        return False
    out = Path(out)
    source = out / 'ai-compute-source.json'
    redrew = False
    if source.is_file() and payload.get('chart_notes') != notes:
        rows = json.loads(source.read_text(encoding='utf-8'))
        if category == 'token_cost':
            token_chart(rows, out, notes=notes)
        elif category == 'gpu_rental':
            actual = payload.get('data_date') or ''
            gpu_chart([row for row in rows if not actual or row['date'] <= actual], out, notes=notes)
        else:
            payload['fixed_notes'] = notes
            return False
        payload['chart_notes'] = notes
        payload['image_sha256'] = {
            name: hashlib.sha256((out / name).read_bytes()).hexdigest()
            for name in payload.get('images') or []
        }
        redrew = True
    payload['fixed_notes'] = notes
    return redrew


def token_cost(day, out, state, refresh=False):
    rows = _rows('token')
    selected_day = os.getenv('AI_COMPUTE_DATA_DATE', '').strip()
    if selected_day:
        rows = [row for row in rows if row['date'] <= selected_day]
    valid = [row for row in rows if row['token_price'] is not None]
    if not valid:
        raise ValueError('Token 支出价格尚无已导入数据')
    latest = valid[-1]
    manual = os.getenv('MANUAL_GENERATION') == '1'
    if latest['date'] != day and not manual:
        raise ValueError(f"Token 数据尚未更新到 {day}；最新已导入为 {latest['date']}")
    actual_day = latest['date']
    record(out, 3, 'Token 支出价格数据校验通过，正在生成历史图')
    public = _public_week(latest, 'token')
    entered = _manual_reading(latest, 'token')
    previous = next((row for row in valid if row['date'] == (date.fromisoformat(actual_day) - timedelta(days=7)).isoformat()), None)
    entered_week = entered.get('week_change_pct') if entered else None
    weekly = public['week_change_pct'] if public else entered_week if entered_week is not None else _pct(latest['token_price'], previous['token_price'] if previous else None)
    recent = [row for row in valid if date.fromisoformat(row['date']) >= date.fromisoformat(actual_day) - timedelta(days=6)]
    low = min((row['token_price'] for row in recent), default=latest['token_price'])
    high = max((row['token_price'] for row in recent), default=latest['token_price'])
    # A narrow five-day tail only counts as *narrowing* when the preceding five
    # calendar days are also complete and had a wider range. A narrow range by
    # itself does not establish that volatility has fallen.
    by_day = {row['date']: _decimal(row['token_price']) for row in valid}
    end = date.fromisoformat(actual_day)
    tail_days = [(end - timedelta(days=offset)).isoformat() for offset in range(4, -1, -1)]
    prior_days = [(end - timedelta(days=offset)).isoformat() for offset in range(9, 4, -1)]
    tail_values = [by_day.get(key) for key in tail_days]
    prior_values = [by_day.get(key) for key in prior_days]
    tail_narrowed = (not (public or entered) and _decimal(latest['token_price']) > 0
                     and all(value is not None for value in tail_values + prior_values)
                     and (max(tail_values) - min(tail_values)) / _decimal(latest['token_price']) <= Decimal('0.01')
                     and max(tail_values) - min(tail_values) < max(prior_values) - min(prior_values))
    peak = max(valid, key=lambda row: (row['token_price'], row['date']))
    price = _decimal(latest['token_price'])
    earlier = [row for row in valid if row['date'] < actual_day]
    if earlier:
        prior_peak = max(earlier, key=lambda row: (row['token_price'], row['date']))
        prior_peak_price = _decimal(prior_peak['token_price'])
        if price > prior_peak_price:
            peak_relation, drawdown_pct = 'above', ''
        elif price == prior_peak_price:
            peak_relation, drawdown_pct = 'tie', ''
        else:
            peak_relation = 'below'
            drawdown_pct = f'{(prior_peak_price - price) / prior_peak_price * 100:.2f}'
        exponent = prior_peak_price.as_tuple().exponent
        places = max(4, -exponent) if isinstance(exponent, int) else 4
        prior_peak_text = f'{prior_peak_price:.{places}f}'
        prior_peak_date = prior_peak['date']
    else:
        peak_relation, drawdown_pct = 'unavailable', ''
        prior_peak_text, prior_peak_date = '', ''
    title_dir = _direction(weekly) if weekly is not None else '持平'
    title = f"【AI算力CPI】Token支出价格指数{title_dir}" if weekly else "【AI算力CPI】Token支出价格指数更新"
    price_text = public['price'] if public else entered['price'] if entered else f'{price:.4f}'
    body = f"截至{actual_day}，Token支出价格指数报{price_text}美元/百万Token"
    if public:
        body += f"，官网公开页展示的七日变化为{title_dir}{abs(Decimal(weekly)):.1f}%"
    elif entered_week is not None:
        body += f"，人工录入的七日变化为{title_dir}{abs(Decimal(weekly))}%"
    elif weekly:
        body += f"，较七个自然日前{title_dir}{abs(Decimal(weekly)):.2f}%"
    else:
        body += "，七个自然日前的基准值缺失，本次不报告周变化"
    if public or entered:
        body += '。'
    else:
        body += f"；最近7日已收录价格区间为{_decimal(low):.4f}—{_decimal(high):.4f}美元。"
    warnings = []
    if actual_day != day:
        warnings.append(f"使用最新可用数据日 {actual_day}，目标数据日 {day} 尚无更新")
    if public:
        warnings.append('公开产品页仅展示Token价格两位小数及七日变化一位小数；未补成工作簿四位精度')
    elif entered:
        warnings.append('本期价格由编辑人工录入；生成前请核对来源和数字')
        if entered_week is None and not previous:
            warnings.append(f'缺少 {actual_day} 七个自然日前的 Token 基准值，本期未报告周变化')
    elif not previous:
        warnings.append(f"缺少 {actual_day} 七个自然日前的 Token 基准值，本期未报告周变化")
    (out/'ai-compute-source.json').write_text(json.dumps(valid,ensure_ascii=False,default=str),encoding='utf-8')
    notes = published_notes('token_cost')
    chart = token_chart(valid, out, notes=notes)
    return {
        'title': title, 'dynamic_body': body, 'images': [chart.name], 'data_date': actual_day,
        'fixed_notes': notes, 'chart_notes': notes,
        'warnings': warnings,
        'facts': {
            'token_price': price_text, 'previous_date': '' if public or entered_week is not None else previous['date'] if previous else '',
            'previous_price': '' if public or entered_week is not None else f"{_decimal(previous['token_price']):.4f}" if previous else '',
            'weekly_change_pct': weekly or '',
            'range_low': '' if public or entered else f'{_decimal(low):.4f}',
            'range_high': '' if public or entered else f'{_decimal(high):.4f}',
            'range_count': 0 if public or entered else len(recent),
            'tail_narrowed': tail_narrowed,
            'tail_start': tail_days[0] if tail_narrowed else '',
            'tail_low': f'{min(tail_values):.4f}' if tail_narrowed else '',
            'tail_high': f'{max(tail_values):.4f}' if tail_narrowed else '',
            'peak_price': f"{_decimal(peak['token_price']):.4f}",
            'peak_date': peak['date'], 'prior_peak_price': prior_peak_text,
            'prior_peak_date': prior_peak_date, 'peak_relation': peak_relation,
            'drawdown_pct': drawdown_pct,
            'source': '编辑人工录入' if entered else 'Silicon Data LLM Token Expenditure Index',
            'source_precision': public['precision'] if public else entered['precision'] if entered else 4,
            'weekly_source': 'manual_entry' if entered_week is not None else 'public_visible_summary' if public else 'calculated',
            'source_note': (latest.get('manual_display') or {}).get('source_note', '') if entered else '',
        },
        'process_details': {
            'sources': [{'label': '编辑人工录入：'+(latest.get('manual_display') or {}).get('source_note', ''), 'url': ''}] if entered else
                       [{'label': 'Silicon Data LLM Token Expenditure Index', 'url': 'https://www.silicondata.com/products/silicon-index/llm-token-expenditure-index'}],
            'inputs': [{'label': 'Token 支出价格', 'value': price_text, 'unit': '美元/百万Token'}],
            'calculations': (['官网公开页直接展示价格及七日变化，保留页面精度；不从两位小数反推四位价格。']
                             if public else ['本期价格使用编辑人工录入值；七日变化优先使用人工录入值，缺失时才按历史同日价格计算。']
                             if entered else ['周变化 = 当日价格 ÷ 七个自然日前价格 − 1；基准日缺失时不替代。',
                                             '最近7日区间只使用已导入的非空价格；历史极值保留日期。']),
        },
    }


def gpu_rental(day, out, state, refresh=False):
    rows = _rows('gpu')
    selected_day = os.getenv('AI_COMPUTE_DATA_DATE', '').strip()
    if selected_day:
        rows = [row for row in rows if row['date'] <= selected_day]
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
    public = {key: _public_week(latest, key) for key in ('a100', 'h100', 'b200')}
    public_mode = all(public.values())
    history_week = {key for key, reading in public.items()
                    if reading and reading.get('week_change_source') == 'visible_history'}
    entered = {key: _manual_reading(latest, key) for key in ('a100', 'h100', 'b200')}
    manual_mode = all(entered.values())
    manual_week = manual_mode and all(entered[key].get('week_change_pct') is not None for key in entered)
    changes = {key: public[key]['week_change_pct'] if public_mode else entered[key]['week_change_pct'] if manual_week else _pct(latest[key], previous[key]) if previous else None
               for key in ('a100', 'h100', 'b200')}
    prices = {key: public[key]['price'] if public_mode else entered[key]['price'] if manual_mode else f'{_decimal(latest[key]):.2f}'
              for key in ('a100', 'h100', 'b200')}
    peaks = {}
    warnings = []
    for key in ('a100', 'h100', 'b200'):
        values = [row for row in rows if row[key] is not None and row['date'] < actual_day]
        if not values:
            raise ValueError(f'{key} 缺少历史峰值数据')
        peaks[key] = max(values, key=lambda row: (row[key], row['date']))
    if actual_day != day:
        warnings.append(f"使用最新可用完整三卡日 {actual_day}，目标数据日 {day} 尚无更新")
    if public_mode:
        if history_week:
            warnings.append('部分七日变化按官网可见历史价格计算并保留一位小数；历史价格经网页舍入，结果不是指数的精确涨跌幅')
            if len(history_week) < 3:
                warnings.append('其余七日变化取自官网公开卡片的一位小数展示值')
        else:
            warnings.append('七日变化取自官网公开卡片的一位小数展示值，未用两位价格反推精确涨跌幅')
    elif manual_mode:
        warnings.append('本期三卡价格由编辑人工录入；生成前请核对来源和数字')
        if not manual_week and not previous:
            warnings.append(f'缺少 {baseline_day} 的完整三卡基准，本期未报告周变化')
    elif not previous:
        warnings.append(f"缺少 {baseline_day} 的完整三卡基准，本期未报告周变化")
    title = f"【AI景气度指标】B200租赁价格报{prices['b200']}美元/GPU每小时"
    parts = [f"B200租赁价格报{prices['b200']}美元/GPU每小时"]
    for label, key in (('H100', 'h100'), ('A100', 'a100')):
        parts.append(f"{label}报{prices[key]}美元/GPU每小时")
    body = f"截至{actual_day}，" + "；".join(parts)
    if public_mode or manual_week:
        body += '。'
        for label, key in (('B200', 'b200'), ('H100', 'h100'), ('A100', 'a100')):
            change = changes[key]
            origin = ('依据官网历史价格计算' if key in history_week else '官网展示') if public_mode else '人工录入'
            body += f'{label}{origin}的七日变化为{_direction(change)}{abs(Decimal(change))}%；'
        body = body.rstrip('；') + '。'
    elif previous:
        body += "。"
        for label, key in (('B200', 'b200'), ('H100', 'h100'), ('A100', 'a100')):
            change = changes[key]
            body += f"{label}较七个自然日前{'上涨' if change and change.startswith('+') and change != '+0.00' else '下跌' if change and change.startswith('-') and change != '-0.00' else '持平'}{abs(Decimal(change)):.2f}%；" if change else f"{label}周变化基准缺失；"
        body = body.rstrip('；') + '。'
    else:
        body += "；周变化基准缺失。"
    peak = peaks['b200']
    peak_price = _decimal(peak['b200'])
    latest_b200 = _decimal(latest['b200'])
    if latest_b200 > peak_price:
        b200_peak_relation, b200_gap_pct = 'above', ''
        body += f"B200处于已收录数据新高（此前高点{peak_price:.2f}美元，{peak['date']}）。"
    elif latest_b200 == peak_price:
        b200_peak_relation, b200_gap_pct = 'tie', ''
        body += f"B200与已收录数据高点{peak_price:.2f}美元持平（{peak['date']}）。"
    else:
        b200_gap_pct = f'{(peak_price - latest_b200) / peak_price * 100:.2f}'
        b200_peak_relation = 'below'
        body += f"B200较已收录数据高点{peak_price:.2f}美元低{b200_gap_pct}%。"
    chart_rows = [row for row in rows if row['date']<=actual_day]
    (out/'ai-compute-source.json').write_text(json.dumps(rows,ensure_ascii=False,default=str),encoding='utf-8')
    notes = published_notes('gpu_rental')
    chart = gpu_chart(chart_rows, out, notes=notes)
    return {
        'title': title, 'dynamic_body': body, 'images': [chart.name], 'data_date': actual_day,
        'fixed_notes': notes, 'chart_notes': notes,
        'warnings': warnings,
        'facts': {
            'a100': prices['a100'], 'h100': prices['h100'],
            'b200': prices['b200'], 'previous_date': '' if public_mode or manual_week else previous['date'] if previous else '',
            'a100_change_pct': changes['a100'] or '', 'h100_change_pct': changes['h100'] or '',
            'b200_change_pct': changes['b200'] or '', 'b200_peak': f"{_decimal(peaks['b200']['b200']):.2f}",
            'b200_peak_date': peaks['b200']['date'], 'b200_peak_relation': b200_peak_relation,
            'b200_gap_pct': b200_gap_pct,
            'source': '编辑人工录入' if manual_mode else 'Silicon Data A100/H100/B200 NeoCloud',
            'weekly_source': ('public_visible_history_mixed' if history_week else 'public_visible_summary') if public_mode else 'manual_entry' if manual_week else 'calculated_from_seven_day_baseline',
            'source_note': (latest.get('manual_display') or {}).get('source_note', '') if manual_mode else '',
        },
        'process_details': {
            'sources': [{'label': '编辑人工录入：'+(latest.get('manual_display') or {}).get('source_note', ''), 'url': ''}] if manual_mode else
                       [{'label': f"{key.upper()} · {actual_day} · " + ('网页历史价格表' if key in history_week else '公开卡片'),
                         'url': public[key].get('source_url') or f'https://www.silicondata.com/products/silicon-index/{key}'}
                        for key in ('a100', 'h100', 'b200')] if public_mode else
                       [{'label': 'Silicon Data GPU Index', 'url': 'https://www.silicondata.com/products/silicon-index'}],
            'inputs': [{'label': 'A100/H100/B200 租赁价格', 'value': f"{prices['a100']}/{prices['h100']}/{prices['b200']}", 'unit': '美元/GPU/小时'}],
            'calculations': (['三卡价格来自同一目标数据日；已更新到更晚日期的卡片改取网页可见历史行，七日变化由该日及七个自然日前的历史价格计算，保留一位小数。' if history_week else '三卡同日公开页面展示价格及七日变化；按页面一位小数精度保留。',
                              '历史峰值按已收录数据计算，等于高点记为并列。'] if public_mode else
                             ['本期三卡价格使用编辑人工录入值；七日变化优先使用人工录入值，缺失时按七日前记录计算。',
                              '历史峰值按已收录数据计算，等于高点记为并列。'] if manual_mode else
                             ['周变化按七个自然日对齐，不按行号；基准日缺失时不补值。',
                              '仅使用同日三卡完整行；历史峰值按各卡已导入的非空序列计算，等于高点记为并列。']),
        },
    }
