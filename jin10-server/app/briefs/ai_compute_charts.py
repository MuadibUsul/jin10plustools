"""Charts for the Token cost and GPU rental fixed briefs."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from matplotlib.font_manager import FontProperties, findfont
from .charts import font

def cjk(size=14):
    for family in ('Noto Sans CJK SC', 'Microsoft YaHei', 'SimHei'):
        try:
            findfont(FontProperties(family=family), fallback_to_default=False)
            return FontProperties(family=family, size=size)
        except Exception:
            continue
    return font(size)

INK = '#17233B'
MUTED = '#7D8798'
BLUE = '#5476DC'
GREEN = '#27A86C'
ORANGE = '#E3A13D'


def token_chart(rows, out):
    values = [(row['date'], float(row.get('price', row.get('token_price')))) for row in rows]
    if not values:
        raise ValueError('Token历史为空')
    fig = plt.figure(figsize=(12, 8.4), dpi=100, facecolor='white')
    ax = fig.add_axes([.1, .34, .84, .5])
    ax.plot([day for day, _ in values], [price for _, price in values], color=BLUE, lw=2.2)
    last_day, last = values[-1]
    ax.scatter([last_day], [last], color=ORANGE, zorder=3, s=42)
    ax.annotate(f'{last:.4f}', (last_day, last), textcoords='offset points',
                xytext=(0, 12), ha='center', fontproperties=cjk(13), color=ORANGE)
    ax.set_title('Token支出价格指数（美元/百万Token）', fontproperties=cjk(20), loc='left', pad=18)
    ticks = sorted(set([0, len(values) - 1] + [round((len(values) - 1) * i / 7) for i in range(1, 7)]))
    ax.set_xticks(ticks, [values[i][0] for i in ticks])
    ax.grid(axis='y', color='#E9EDF3')
    ax.tick_params(colors=MUTED, labelsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    # Local 30-day view beneath the full history.
    local = values[-31:]
    ax2 = fig.add_axes([.1, .1, .84, .16])
    ax2.plot([day for day, _ in local], [price for _, price in local], color=GREEN, lw=2)
    ax2.set_title('最近30日', fontproperties=cjk(14), loc='left', pad=8)
    local_ticks = sorted(set([0, len(local) - 1] + [round((len(local) - 1) * i / 4) for i in range(1, 4)]))
    ax2.set_xticks(local_ticks, [local[i][0] for i in local_ticks])
    ax2.grid(axis='y', color='#E9EDF3')
    ax2.tick_params(colors=MUTED, labelsize=8)
    for spine in ax2.spines.values():
        spine.set_visible(False)
    fig.text(.1, .93, 'PLUS 快讯 / AI算力CPI', fontproperties=cjk(14), color=MUTED)
    fig.text(.1, .04, '数据来源：Silicon Data · LLM Token Expenditure Index', fontproperties=cjk(11), color=MUTED)
    path = Path(out) / 'AI算力CPI.png'
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path


def gpu_chart(rows, out):
    series = [('A100', 'a100', BLUE), ('H100', 'h100', GREEN), ('B200', 'b200', ORANGE)]
    fig = plt.figure(figsize=(12, 8.4), dpi=100, facecolor='white')
    ax = fig.add_axes([.1, .15, .84, .68])
    labels = []
    for label, key, color in series:
        points = [(row['date'], float(row[key])) for row in rows if row.get(key) is not None]
        if not points:
            continue
        labels.append(label)
        ax.plot([day for day, _ in points], [value for _, value in points],
                color=color, lw=2, label=label)
        ax.annotate(f'{points[-1][1]:.2f}', (points[-1][0], points[-1][1]),
                    textcoords='offset points', xytext=(6, 4), fontproperties=cjk(11), color=color)
    ax.set_title('AI芯片租赁价格（美元/GPU/小时，NeoCloud）', fontproperties=cjk(20), loc='left', pad=18)
    all_days = [row['date'] for row in rows]
    ticks = sorted(set([0, len(all_days) - 1] + [round((len(all_days) - 1) * i / 7) for i in range(1, 7)]))
    ax.set_xticks(ticks, [all_days[i] for i in ticks])
    ax.grid(axis='y', color='#E9EDF3')
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.legend(prop=font(12), frameon=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.text(.1, .93, 'PLUS 快讯 / AI景气度指标', fontproperties=cjk(14), color=MUTED)
    fig.text(.1, .04, '数据来源：Silicon Data · A100/H100/B200 NeoCloud', fontproperties=cjk(11), color=MUTED)
    path = Path(out) / 'AI景气度指标.png'
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path
