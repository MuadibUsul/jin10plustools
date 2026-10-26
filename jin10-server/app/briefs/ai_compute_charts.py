"""Product charts for the Token-cost and GPU-rental fixed briefs.

Regenerating either brief calls these functions and gets this layout: one panel,
a thin horizontal date band, the legend centered under the plot, and the source,
unit and latest readings as small footer text. ``image_delivery`` adds the PLUS
watermark and the size cap afterwards.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import math
import warnings

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont, fontManager
from matplotlib.patheffects import Normal, Stroke
from matplotlib.ticker import FuncFormatter

from .charts import FONT

# PlusBriefs is a subset. Production images ship Noto Sans CJK; other machines
# may only have WenQuanYi or Droid. Skip the variable fallback face — matplotlib
# selects its hairline weight and logs a warning on every label.
_CJK_CANDIDATES = (
    'Noto Sans CJK SC', 'Noto Sans CJK JP', 'Noto Sans CJK TC',
    'WenQuanYi Micro Hei', 'Droid Sans Fallback', 'Microsoft YaHei', 'SimHei',
)
INK = '#17233B'
MUTED = '#7D8798'
# Series colors follow the reference product charts; text stays on the PLUS ink.
TOKEN_COLOR = '#1FB6C8'
H100_COLOR = '#8B7CC8'
B200_COLOR = '#2EC4D4'
A100_COLOR = '#E3B341'
HALO = [Stroke(linewidth=5.2, foreground='white', joinstyle='round', capstyle='round'), Normal()]
# Plot rectangle: left, bottom, width, height. The legend centers on this panel.
AXES = (0.078, 0.205, 0.89, 0.675)
_FAMILY = None


def _install_fonts():
    global _FAMILY
    if _FAMILY is not None:
        return
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        fontManager.addfont(str(FONT))
    family = ['PlusBriefs']
    for name in _CJK_CANDIDATES:
        try:
            findfont(FontProperties(family=name), fallback_to_default=False)
        except Exception:
            continue
        family.append(name)
    _FAMILY = family


def _rc():
    return {
        'font.family': 'sans-serif',
        'font.sans-serif': _FAMILY,
        'axes.unicode_minus': False,
        'figure.facecolor': 'white',
        'savefig.facecolor': 'white',
    }


def _type(size):
    return FontProperties(family=_FAMILY, size=size)


def _ordered(rows):
    return sorted(rows, key=lambda row: row['date'])


def _days(rows):
    return [date.fromisoformat(row['date']) for row in rows]


def compact_date_ticks(days):
    """A few horizontal labels in ``2026/10`` or ``10/7`` form, never ISO dates.

    A handful of observations keep one tick each so a short series still lines
    up with its dates. Longer history thins to month or evenly spaced marks.
    """
    unique = sorted(set(days))
    if not unique:
        return [], []
    span = (unique[-1] - unique[0]).days
    if len(unique) <= 8:
        return unique, [_tick_text(day, span) for day in unique]
    if span > 120:
        return _month_ticks(unique)
    chosen = _even(unique, 7)
    return chosen, [_tick_text(day, span) for day in chosen]


def _tick_text(day, span):
    if span > 120:
        return f'{day.year}/{day.month}'
    return f'{day.month}/{day.day}'


def _even(days, limit):
    if len(days) <= limit:
        return days
    indexes = sorted({round(i * (len(days) - 1) / (limit - 1)) for i in range(limit)})
    return [days[index] for index in indexes]


def _month_ticks(days):
    start, end = days[0], days[-1]
    months = []
    year, month = start.year, start.month
    while True:
        cursor = date(year, month, 1)
        if cursor > end:
            break
        months.append(cursor if cursor >= start else start)
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    chosen = _stride(months, 7)
    return chosen, [f'{day.year}/{day.month}' for day in chosen]


def _stride(items, limit):
    if len(items) <= limit:
        return items
    stride = 2
    while stride < len(items):
        chosen = items[::stride]
        if chosen[-1] != items[-1]:
            chosen = chosen + [items[-1]]
        deduped = []
        for item in chosen:
            if not deduped or deduped[-1] != item:
                deduped.append(item)
        if len(deduped) <= limit:
            return deduped
        stride += 1
    return [items[0], items[-1]]


def _apply_time_axis(ax, days):
    ticks, labels = compact_date_ticks(days)
    ax.set_xticks(ticks, labels)
    for label in ax.get_xticklabels():
        label.set_fontproperties(_type(8))
        label.set_color(MUTED)
        label.set_rotation(0)
        label.set_ha('center')
        label.set_va('top')
    ax.tick_params(axis='x', length=0, pad=1, colors=MUTED, labelrotation=0)
    if len(days) >= 2:
        pad = max((days[-1] - days[0]).days * 0.012, 0.6)
        ax.set_xlim(days[0] - timedelta(days=pad), days[-1] + timedelta(days=pad))


def _ceiling(top, step):
    if top <= 0:
        return step
    ceiling = math.ceil((top * 1.06) / step) * step
    if ceiling <= top:
        ceiling += step
    return ceiling


def _gpu_tick(value, _pos):
    return f'{value:.0f}' if abs(value - round(value)) < 1e-6 else f'{value:.1f}'


def _apply_price_axis(ax, values, step, formatter):
    finite = [value for value in values if math.isfinite(value)]
    top = max(finite) if finite else step
    bottom = min(0.0, min(finite) if finite else 0.0)
    ceiling = _ceiling(top, step)
    count = int(round((ceiling - bottom) / step))
    ax.set_yticks([bottom + step * index for index in range(count + 1)])
    ax.set_ylim(bottom, ceiling)
    ax.yaxis.set_major_formatter(FuncFormatter(formatter))
    ax.set_yscale('linear')
    for label in ax.get_yticklabels():
        label.set_fontproperties(_type(9))
        label.set_color(MUTED)
    ax.tick_params(axis='y', length=0, pad=3, colors=MUTED)


def _frame(ax):
    ax.set_axisbelow(True)
    ax.grid(axis='y', color='#E6EAF0', lw=0.8)
    ax.grid(axis='x', visible=False)
    for spine in ax.spines.values():
        spine.set_visible(False)


def _plot(ax, days, values, color, label):
    line, = ax.plot(days, values, color=color, lw=2.25, solid_capstyle='round',
                     solid_joinstyle='round', label=label, zorder=3, path_effects=HALO)
    return line


def _legend(fig, handles, labels):
    if not handles:
        return None
    return fig.legend(
        handles, labels, prop=_type(11), frameon=False, loc='upper center',
        bbox_to_anchor=(AXES[0] + AXES[2] / 2, 0.168), bbox_transform=fig.transFigure,
        ncol=max(1, len(handles)), handlelength=1.7, handletextpad=0.4,
        columnspacing=1.45, borderaxespad=0,
    )


def _header(fig, kicker, title):
    fig.text(0.07, 0.978, kicker, fontproperties=_type(11), color=MUTED, ha='left', va='top')
    fig.text(0.5, 0.932, title, fontproperties=_type(17), color=INK, ha='center', va='center')


def _footer(fig, lines):
    slots = {
        1: (0.028,),
        2: (0.050, 0.022),
        3: (0.074, 0.048, 0.022),
        4: (0.096, 0.072, 0.048, 0.022),
    }
    for line, y in zip(lines, slots.get(len(lines), slots[4])):
        fig.text(0.07, y, line, fontproperties=_type(9), color=MUTED, ha='left', va='bottom')


def _display(row):
    if not row:
        return {}
    if row.get('status') == 'manual_entry':
        return row.get('manual_display') or {}
    return row.get('public_display') or {}


def _reading(row, metric):
    matches = [item for item in (_display(row).get('readings') or [])
               if item.get('metric') == metric and item.get('date') == row.get('date')]
    return matches[0] if len(matches) == 1 else None


def _price_text(row, metric, value, places):
    reading = _reading(row, metric)
    if reading and reading.get('price') not in (None, ''):
        return str(reading['price'])
    return f'{Decimal(str(value)):.{places}f}'


def _stored(row, key):
    if key == 'token_price':
        value = row.get('token_price')
        return row.get('price') if value is None else value
    return row.get(key)


def _week_text(row, metric, rows, key):
    reading = _reading(row, metric)
    if reading and reading.get('week_change_pct') not in (None, ''):
        return _signed_percent(reading['week_change_pct'])
    current = _stored(row, key)
    if current is None:
        return None
    target = (date.fromisoformat(row['date']) - timedelta(days=7)).isoformat()
    previous = next((item for item in rows if item.get('date') == target and _stored(item, key) is not None), None)
    if previous is None:
        return None
    base = Decimal(str(_stored(previous, key)))
    if base == 0:
        return None
    change = (Decimal(str(current)) / base - 1) * 100
    return f'{change:+.2f}%'


def _signed_percent(raw):
    text = str(raw).strip()
    if text.endswith('%'):
        return text
    number = Decimal(text)
    if number > 0 and not text.startswith('+'):
        text = '+' + text
    return f'{text}%'


def _dated(day):
    return f'{day.year}/{day.month}/{day.day}'


def _source(row, automatic):
    if row.get('status') == 'manual_entry':
        return '历史工作簿及本期编辑人工录入'
    return automatic


def _note_lines(notes):
    if notes is None:
        return []
    text = ' '.join(str(notes).split())
    return [text] if text else []


def _figure():
    fig = plt.figure(figsize=(12, 6.5), dpi=100, facecolor='white')
    # Bottom band is labels + a compact legend + the footer, not a second panel.
    ax = fig.add_axes(list(AXES))
    return fig, ax


def _save(fig, out, name):
    path = Path(out) / name
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path


def token_chart(rows, out, *, notes=None):
    rows = _ordered(rows)
    values = [(date.fromisoformat(row['date']), float(row.get('price', row.get('token_price')))) for row in rows]
    if not values:
        raise ValueError('Token历史为空')
    _install_fonts()
    with plt.rc_context(_rc()):
        fig, ax = _figure()
        days = [day for day, _ in values]
        prices = [price for _, price in values]
        line = _plot(ax, days, prices, TOKEN_COLOR, '价格（美元/百万Token）')
        _apply_time_axis(ax, days)
        _apply_price_axis(ax, prices, 0.5 if max(prices) > 1.2 else 0.2,
                          lambda value, _pos: f'${value:.2f}')
        _frame(ax)
        _legend(fig, [line], ['价格（美元/百万Token）'])
        _header(fig, 'PLUS 快讯 / AI算力CPI', 'Token支出价格指数')
        latest = rows[-1]
        shown = _price_text(latest, 'token', prices[-1], 4)
        week = _week_text(latest, 'token', rows, 'token_price')
        summary = f'最新 {shown} 美元/百万Token（{_dated(days[-1])}'
        summary += f'，七日 {week}）' if week else '）'
        _footer(fig, [
            summary,
            '数据来源：' + _source(latest, 'Silicon Data · LLM Token Expenditure Index'),
            *_note_lines(notes),
        ])
        return _save(fig, out, 'AI算力CPI.png')


def gpu_chart(rows, out, *, notes=None):
    rows = _ordered(rows)
    _install_fonts()
    with plt.rc_context(_rc()):
        fig, ax = _figure()
        # Draw A100 first so a missing observation stays a gap on that series.
        series = [('A100', 'a100', A100_COLOR), ('H100', 'h100', H100_COLOR), ('B200', 'b200', B200_COLOR)]
        days = _days(rows)
        handles = {}
        finite = []
        for label, key, color in series:
            values = [float(row[key]) if row.get(key) is not None else math.nan for row in rows]
            if not any(math.isfinite(value) for value in values):
                continue
            handles[label] = _plot(ax, days, values, color, f'{label}价格')
            finite.extend(value for value in values if math.isfinite(value))
        if days:
            _apply_time_axis(ax, days)
        step = 1 if (max(finite) if finite else 1) <= 8 else 2
        _apply_price_axis(ax, finite or [1], step, _gpu_tick)
        _frame(ax)
        order = [name for name in ('H100', 'B200', 'A100') if name in handles]
        _legend(fig, [handles[name] for name in order], [f'{name}价格' for name in order])
        _header(fig, 'PLUS 快讯 / AI景气度指标', 'AI芯片租赁价格走势（美元/每块GPU每小时）')
        _footer(fig, _gpu_footer(rows, notes))
        return _save(fig, out, 'AI景气度指标.png')


def _gpu_footer(rows, notes):
    if not rows:
        return ['数据来源：Silicon Data · A100/H100/B200 NeoCloud', *_note_lines(notes)]
    cards = (('A100', 'a100'), ('H100', 'h100'), ('B200', 'b200'))
    latest = {}
    for label, key in cards:
        found = next((row for row in reversed(rows) if row.get(key) is not None), None)
        if found:
            latest[label] = (found, key)
    if not latest:
        lines = ['数据来源：' + _source(rows[-1], 'Silicon Data · A100/H100/B200 NeoCloud')]
        return lines + _note_lines(notes)
    dates = {row['date'] for row, _key in latest.values()}
    parts = []
    for label, _key in cards:
        if label not in latest:
            continue
        row, key = latest[label]
        parts.append(f'{label} {_price_text(row, key, row[key], 2)}')
    if len(dates) == 1:
        summary = f"最新 {' · '.join(parts)} 美元/GPU/小时（{_dated(date.fromisoformat(next(iter(dates))))}）"
    else:
        detailed = []
        for label, _key in cards:
            if label not in latest:
                continue
            row, key = latest[label]
            detailed.append(f'{label} {_price_text(row, key, row[key], 2)}（{_dated(date.fromisoformat(row["date"]))}）')
        summary = f"最新 {' · '.join(detailed)} 美元/GPU/小时"
    weeks = []
    for label, key in cards:
        if label not in latest:
            continue
        row, series_key = latest[label]
        week = _week_text(row, key, rows, series_key)
        if week:
            weeks.append(f'{label} {week}')
    source = '数据来源：' + _source(rows[-1], 'Silicon Data · A100/H100/B200 NeoCloud')
    lines = [summary]
    if weeks:
        lines.append(f"七日 {' · '.join(weeks)}")
    lines.append(source)
    return lines + _note_lines(notes)
