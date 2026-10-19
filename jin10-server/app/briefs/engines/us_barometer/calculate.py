"""US barometer calculations and the verdict wording used in the brief."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

Q = Decimal('0.01')


def q2(value):
    return Decimal(str(value)).quantize(Q, rounding=ROUND_HALF_UP)


def gdp_asof(gdp_rows, day):
    eligible = [row for row in gdp_rows if row['period'] <= day]
    if not eligible:
        raise ValueError('缺少目标日期之前已发布的GDP数据')
    return eligible[-1]


def buffett_series(wilshire_rows, gdp_rows):
    rows = []
    for row in wilshire_rows:
        quarter = gdp_asof(gdp_rows, row['date'])
        rows.append({'date': row['date'], 'pct': q2(Decimal(str(row['close'])) / Decimal(str(quarter['value'])) * 100)})
    return rows


def erp_series(erp_rows):
    return [{'date': row['date'], 'pct': q2(Decimal(str(row['erp'])) * 100)} for row in erp_rows]


BUFFETT_GRADES = (
    (200, '严重高估', -2, '处于严重高估水平，股市表现已经超过合理区间，需要有进一步高增长业绩出现才能支撑当前估值。'),
    (150, '高估', -1, '处于高估水平，明显高于合理区间，需要持续的高增长业绩来消化估值。'),
    (100, '偏高', 0, '处于偏高区间，估值偏贵，宜留意业绩兑现节奏。'),
    (70, '合理', 1, '处于合理区间，估值与经济增长基本匹配。'),
    (0, '低估', 2, '处于低估区间，股市规模相对经济明显偏低，通常对应更好的长期回报。'),
)
ERP_GRADES = (
    (7.0, '很高', 2, '处于很高水平，股票明显比国债便宜，历史上多对应底部区域或较好的买入机会。'),
    (6.0, '偏高', 1, '处于偏高水平，股票相对国债仍具一定吸引力。'),
    (5.5, '中性', 0, '处于中性水平，股票与国债的性价比大致相当。'),
    (4.5, '偏低', -1, '处于偏低水平，暗示目前股市相对国债收益并无优势。'),
    (0.0, '很低', -2, '处于很低水平，股票明显比国债昂贵，买股票承担了较高风险却难以获得应有的回报。'),
)
VERDICTS = ((4, '高'), (2, '较高'), (-1, '中性'), (-3, '较低'))


def grade(value, table):
    number = float(value)
    for threshold, label, score, text in table:
        if number >= threshold:
            return {'label': label, 'score': score, 'text': text}
    raise ValueError('指标超出可判断范围')


def overall(buffett_score, erp_score):
    total = buffett_score + erp_score
    for floor, label in VERDICTS:
        if total >= floor:
            return label, total
    return '低', total


def verdict_copy(day, buffett, erp):
    """Title and body for the US barometer issue, matching the approved sample wording."""
    from datetime import date
    parsed = date.fromisoformat(day) if isinstance(day, str) else day
    buffett_grade = grade(buffett, BUFFETT_GRADES)
    erp_grade = grade(erp, ERP_GRADES)
    label, score = overall(buffett_grade['score'], erp_grade['score'])
    title = f'【美股晴雨表】美股{parsed.month}月{parsed.day}日投资性价比：{label}'
    body = (f'1、巴菲特指数：{buffett}%，{buffett_grade["text"]}\n'
            f'2、美国标普500指数股权风险溢价（ERP）：{erp}%，{erp_grade["text"]}')
    return {
        'title': title, 'body': body, 'label': label,
        'buffett': {'value': str(buffett), 'grade': buffett_grade['label'], 'text': buffett_grade['text']},
        'erp': {'value': str(erp), 'grade': erp_grade['label'], 'text': erp_grade['text']},
        'score': score,
    }
