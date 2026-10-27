import copy
import json
from pathlib import Path
import pytest

from app.briefs.engines.liquidity.render import render_poster, EXPLANATION
from app.briefs.engines.liquidity.fetch_data import SourceError, parse_quotes
from app.briefs.delivery import default_notes
from app.services.image_delivery import store_image, WATERMARK, MAX_BYTES


def sample_snapshot():
    return {'quotes': {'quote_date': '2026-10-15', 'updated_at': '2026-10-15 17:00',
        'rows': [{'product': code, 'weighted_rate': '1.3500', 'latest_rate': '1.3500',
                  'average_term_days': str(days), 'updated_at': '2026-10-15 17:00'}
                 for code, days in [('DR001', 1), ('DR007', 7), ('DR014', 14), ('DR021', 21)]]},
        'history': {'start': '2026-10-14', 'end': '2026-10-15', 'series': ['DR001', 'DR007', 'DR014'],
                    'rows': [{'date': day, 'DR001': value, 'DR007': '1.35', 'DR014': '1.36'}
                             for day, value in [('2026-10-14', '1.31'), ('2026-10-15', '1.30')]]}}


def test_liquidity_table_time_and_editable_notes(tmp_path):
    snapshot = sample_snapshot()
    before = copy.deepcopy(snapshot)
    png, svg, audit = render_poster(snapshot, tmp_path / 'original', logo_path=WATERMARK)
    assert snapshot == before
    text = svg.read_text(encoding='utf-8')
    assert '数据更新时间' not in text and text.count('17:00') == 1
    assert len(audit['table_columns']) == 4 and len(audit['table_cells']) == 12
    assert audit['explanation_text'] == default_notes('liquidity')
    assert '#F2F9FF' in text and 'DR001与隔夜逆回购利率之差' in text
    assert EXPLANATION == tuple(default_notes('liquidity').splitlines())
    delivered, _ = store_image(png, tmp_path / 'delivery', reuse_footer=True)
    assert Path(delivered).stat().st_size <= MAX_BYTES


def test_quotation_requires_previous_day_close_and_both_dr_rates():
    payload = {'head': {'rep_code': '200'}, 'data': {'showDateCN': '2026-10-15 17:00'},
               'records': [{'productCode': code, 'date': '26-10-15', 'weightedRate': '1.3500',
                            'latestRate': '1.3500', 'avgPrd': '1'} for code in ('DR001','DR007')]}
    raw = lambda: json.dumps(payload).encode()
    assert parse_quotes(raw(), '2026-10-15')['quote_date'] == '2026-10-15'
    payload['data']['showDateCN'] = '2026-10-15 16:45'
    with pytest.raises(SourceError, match='17:00'):
        parse_quotes(raw(), '2026-10-15')
    payload['data']['showDateCN'] = '2026-10-15 17:00'
    payload['records'].pop(0)
    with pytest.raises(SourceError, match='DR001'):
        parse_quotes(raw(), '2026-10-15')
