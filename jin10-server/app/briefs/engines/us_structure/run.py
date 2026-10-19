"""Fetch, validate and render the US market-structure brief."""
from __future__ import annotations

from datetime import date, timedelta

from app.briefs.progress import record

from . import fetch_data
from .calculate import structure

SNAPSHOT = 'us-structure-snapshot.json'


def window(rows, day, years=10):
    end = date.fromisoformat(day)
    try:
        start = end.replace(year=end.year - years)
    except ValueError:
        start = end.replace(year=end.year - years, day=28)
    return [row for row in rows if start.isoformat() <= row['date'] <= end.isoformat()]


def fetch(day, out):
    record(out, 2, '正在获取三大指数、罗素2000 与标普500等权指数')
    data = fetch_data.fetch()
    for name, rows in data.items():
        if not any(row['date'] == day for row in rows):
            raise ValueError(f'{"标普500等权指数" if name == "spxew" else name.upper()} 目标日期尚未更新')
    return {'target': day, 'data': {name: window(rows, day) for name, rows in data.items()},
            'sources': {'yahoo': 'https://query1.finance.yahoo.com/v8/finance/chart/'}}


def render(snapshot, out):
    day = snapshot['target']
    result = structure(day, snapshot['data'])
    from app.briefs.charts import us_structure_chart
    record(out, 3, '指数涨跌与宽度差值已校验，正在绘制市场结构图表')
    chart = us_structure_chart(result['gap_rows'], out)
    return {
        'title': result['title'], 'dynamic_body': result['body'], 'images': [chart.name],
        'data_date': day, 'facts': {**result['facts'], 'sources': snapshot['sources']}, 'warnings': [],
    }
