"""Product charts for the Token-cost and GPU-rental fixed briefs.

Regenerating either brief calls these functions and gets this layout: one panel,
a thin horizontal date band, the legend centered under the plot, and the brief's
editorial notes as small wrapped footer text. The source, unit and latest-reading
caption is not drawn here. ``image_delivery`` adds the PLUS watermark and the
size cap afterwards.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import math
import warnings

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont, fontManager
from matplotlib.lines import Line2D
from matplotlib.patheffects import Normal, SimpleLineShadow, Stroke
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
NOTE_COLOR = '#59677A'
# Reference product chart: teal B200, lavender H100, yellow-gold A100.
# These are not the older blue / green / orange brief palette.
B200_COLOR = '#2EC8D4'
H100_COLOR = '#9B8CC9'
A100_COLOR = '#F0C446'
TOKEN_COLOR = B200_COLOR
SERIES_COLORS = {'A100': A100_COLOR, 'H100': H100_COLOR, 'B200': B200_COLOR}
# The embedded Excel charts reference Sheet1!A3:A311 (309 Token readings) and
# Sheet2!A3:A371 (367 populated GPU readings plus two blank header/future rows).
# Keep the same rolling number of observed dates when new rows are imported.
TOKEN_VISIBLE_OBSERVATIONS = 309
GPU_VISIBLE_OBSERVATIONS = 367


def _line_shadow(offset, width, alpha):
    return SimpleLineShadow(offset=offset, shadow_color='#A9B4BF', alpha=alpha, linewidth=width)


# A soft gray drop shadow plus a light rim, then the solid series color.
# A white stroke alone disappears on the white page and leaves the line flat.
LINE_EFFECTS = [
    _line_shadow((0.8, -2.4), 7.2, 0.22),
    _line_shadow((0.3, -1.2), 5.2, 0.32),
    Stroke(linewidth=4.4, foreground='#F5F7F9'),
    Normal(),
]
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
    line, = ax.plot(days, values, color=color, lw=2.35, solid_capstyle='round',
                     solid_joinstyle='round', label=label, zorder=3, path_effects=LINE_EFFECTS)
    return line


def _swatch(color):
    """Legend sample in the series color only, without the plot shadow."""
    return Line2D([0], [0], color=color, lw=2.6, solid_capstyle='round')


def _legend(fig, handles, labels, *, expanded=False):
    if not handles:
        return None
    if expanded and fig.axes:
        pos = fig.axes[0].get_position()
        anchor_y = pos.y0 - (0.20 / fig.get_figheight())
        anchor_x = pos.x0 + pos.width / 2
    else:
        anchor_x = AXES[0] + AXES[2] / 2
        anchor_y = 0.168
    return fig.legend(
        handles, labels, prop=_type(11), frameon=False, loc='upper center',
        bbox_to_anchor=(anchor_x, anchor_y), bbox_transform=fig.transFigure,
        ncol=max(1, len(handles)), handlelength=1.7, handletextpad=0.4,
        columnspacing=1.45, borderaxespad=0,
    )


def _header(fig, title):
    fig.text(0.5, 0.945, title, fontproperties=_type(17), color=INK, ha='center', va='center')


def _measure(text):
    from matplotlib.textpath import TextPath
    if not text:
        return 0.0
    return TextPath((0, 0), text, size=NOTE_SIZE, prop=_type(NOTE_SIZE)).get_extents().width


def _wrap_paragraph(paragraph):
    if _measure(paragraph) <= _NOTE_WIDTH_PT:
        return [paragraph]
    lines, buf = [], ''
    for char in paragraph:
        trial = buf + char
        if buf and _measure(trial) > _NOTE_WIDTH_PT:
            lines.append(buf)
            buf = char
        else:
            buf = trial
    if buf:
        lines.append(buf)
    return lines or [paragraph]


def _wrap_notes(notes):
    """Keep each numbered bullet, wrapping only when a line does not fit."""
    text = '' if notes is None else str(notes).strip()
    if not text:
        return []
    lines = []
    for paragraph in text.splitlines():
        paragraph = paragraph.strip()
        if paragraph:
            lines.extend(_wrap_paragraph(paragraph))
    return lines


def _category_notes(category, notes):
    if notes is None:
        from .delivery import default_notes
        return default_notes(category)
    return str(notes).strip()


def _footer(fig, lines):
    if not lines:
        return
    font = _type(NOTE_SIZE)
    if len(lines) <= 4 and abs(fig.get_figheight() - _STANDARD_HEIGHT) < 0.01:
        slots = {
            1: (0.028,),
            2: (0.050, 0.022),
            3: (0.074, 0.048, 0.022),
            4: (0.096, 0.072, 0.048, 0.022),
        }
        for line, y in zip(lines, slots[len(lines)]):
            fig.text(0.07, y, line, fontproperties=font, color=NOTE_COLOR, ha='left', va='bottom')
        return
    pos = fig.axes[0].get_position()
    line_frac = (NOTE_SIZE * 1.45 / 72) / fig.get_figheight()
    y = pos.y0 - (0.62 / fig.get_figheight())
    for line in lines:
        fig.text(0.07, y, line, fontproperties=font, color=NOTE_COLOR, ha='left', va='top')
        y -= line_frac


# Four short lines fit the standard 6.5in page. Longer notes grow the page
# downward so the plot keeps its height and the footer stays under the legend.
_STANDARD_HEIGHT = 6.5
NOTE_SIZE = 9.2
_NOTE_WIDTH_PT = 730


def _figure(line_count=0):
    extra = max(0, line_count - 4)
    height = _STANDARD_HEIGHT + extra * 0.26
    fig = plt.figure(figsize=(12, height), dpi=100, facecolor='white')
    if extra == 0:
        # Bottom band is labels + a compact legend + the footer, not a second panel.
        ax = fig.add_axes(list(AXES))
        return fig, ax
    plot_in = AXES[3] * _STANDARD_HEIGHT
    header_in = _STANDARD_HEIGHT * (1 - (AXES[1] + AXES[3]))
    footer_in = height - header_in - plot_in
    ax = fig.add_axes([AXES[0], footer_in / height, AXES[2], plot_in / height])
    return fig, ax


def _save(fig, out, name):
    path = Path(out) / name
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path


def token_chart(rows, out, *, notes=None):
    rows = _ordered(rows)[-TOKEN_VISIBLE_OBSERVATIONS:]
    values = [(date.fromisoformat(row['date']), float(row.get('price', row.get('token_price')))) for row in rows]
    if not values:
        raise ValueError('Token历史为空')
    _install_fonts()
    lines = _wrap_notes(_category_notes('token_cost', notes))
    with plt.rc_context(_rc()):
        fig, ax = _figure(len(lines))
        days = [day for day, _ in values]
        prices = [price for _, price in values]
        _plot(ax, days, prices, TOKEN_COLOR, '价格（美元/百万Token）')
        _apply_time_axis(ax, days)
        _apply_price_axis(ax, prices, 0.5 if max(prices) > 1.2 else 0.2,
                          lambda value, _pos: f'${value:.2f}')
        _frame(ax)
        _legend(fig, [_swatch(TOKEN_COLOR)], ['价格（美元/百万Token）'], expanded=len(lines) > 4)
        _header(fig, 'Silicon Data Token支出价格指数')
        _footer(fig, lines)
        return _save(fig, out, 'AI算力CPI.png')


def gpu_chart(rows, out, *, notes=None):
    rows = _ordered(rows)[-GPU_VISIBLE_OBSERVATIONS:]
    _install_fonts()
    lines = _wrap_notes(_category_notes('gpu_rental', notes))
    with plt.rc_context(_rc()):
        fig, ax = _figure(len(lines))
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
        _legend(fig, [_swatch(SERIES_COLORS[name]) for name in order], [f'{name}价格' for name in order],
                expanded=len(lines) > 4)
        _header(fig, 'AI芯片租赁价格走势（美元/每块GPU每小时）')
        _footer(fig, lines)
        return _save(fig, out, 'AI景气度指标.png')
