import copy
from pathlib import Path

from app.briefs.engines.liquidity.render import render_poster, EXPLANATION
from app.services.image_delivery import store_image, WATERMARK, MAX_BYTES


def test_liquidity_table_time_and_inline_template(tmp_path):
    snapshot = {'quotes': {'quote_date': '2026-10-15', 'updated_at': '2026-10-15 17:00',
        'rows': [{'product': code, 'weighted_rate': '1.3500', 'latest_rate': '1.3500',
                  'average_term_days': str(days), 'updated_at': '2026-10-15 17:00'}
                 for code, days in [('DR001', 1), ('DR007', 7), ('DR014', 14), ('DR021', 21)]]},
        'history': {'start': '2026-10-14', 'end': '2026-10-15', 'series': ['DR001', 'DR007', 'DR014'],
                    'rows': [{'date': day, 'DR001': value, 'DR007': '1.35', 'DR014': '1.36'}
                             for day, value in [('2026-10-14', '1.31'), ('2026-10-15', '1.30')]]}}
    before = copy.deepcopy(snapshot)
    png, svg, audit = render_poster(snapshot, tmp_path / 'original', logo_path=WATERMARK)
    assert snapshot == before
    text = svg.read_text(encoding='utf-8')
    assert '数据更新时间' not in text and text.count('17:00') == 1
    assert len(audit['table_columns']) == 4 and len(audit['table_cells']) == 12
    assert all(label['inline'] and label['text_y'] == label['pill_y'] for label in audit['explanation_inline_labels'])
    assert '#F2F9FF' in text and '较为宽松' not in text
    assert EXPLANATION[-1] == 'DR007低于或接近政策利率：说明市场资金宽松。'
    delivered, _ = store_image(png, tmp_path / 'delivery', reuse_footer=True)
    assert Path(delivered).stat().st_size <= MAX_BYTES
