import hashlib
import io
from datetime import date, timedelta

import pytest
from PIL import Image

from app.briefs.engines.us_structure import fetch_data
from app.briefs.engines.us_structure import run
from app.briefs.engines.us_structure.calculate import structure
from app.services.image_delivery import MAX_BYTES, compose_image, encode_image

DAY = '2026-10-14'


def fixtures():
    """Synthetic closes reproduce the approved sample wording for 2026-10-14."""
    start = date(2026, 4, 15)
    days = []
    cursor = start
    while cursor <= date(2026, 10, 14):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    gaps = {day: 1000.0 for day in days}
    gaps['2026-10-07'] = 734.35  # the actual low made this month
    gaps['2026-07-15'] = 1424.12  # one-year high
    gaps[days[-2]] = 837.38
    gaps[days[-1]] = 873.01

    def rows(close, low=None, last_close=None):
        out = []
        for index, day in enumerate(days):
            value = last_close if day == days[-1] else close
            out.append({'date': day, 'close': value, 'low': low if low is not None else value - 10})
        return out

    data = {
        'spx': rows(7806.50, last_close=7718.94),
        'ndx': rows(26640.0, last_close=26140.0),
        'dji': rows(52049.0, last_close=52002.0),
        'spxew': [{'date': row['date'], 'close': row['close'] + gaps[row['date']],
                   'low': row['close'] + gaps[row['date']]} for row in rows(7806.50, last_close=7718.94)],
    }
    rut = []
    for day in days:
        close = 2783.37 if day != days[-1] else 2729.53
        low = 2800.0 if day != days[-1] else 2720.04
        if day == '2026-04-16':
            low = 2700.0
        if day == '2026-05-20':
            close = 3068.42
        rut.append({'date': day, 'close': close, 'low': low})
    data['rut'] = rut
    return data


def test_sample_wording_is_reproduced_exactly():
    result = structure(DAY, fixtures())
    assert result['title'] == '【美股晴雨表】美股10月14日市场结构：市场宽度依然不佳'
    assert result['body'] == (
        '美股市场结构：小盘股率先进入调整区间，但巨头也开始承压。\n'
        '1、前一个交易日纳斯达克指数收跌1.88%、标普500指数收跌1.12%、道琼斯指数收跌0.09%，'
        '标普500等权指数与市值加权指数之差从本月刷新的近一年低点反弹，'
        '暗示权重股承受压力更大，但仍处于年内偏低水平，市场宽度改善程度有限；\n'
        '2、代表小盘股的罗素2000指数也收跌1.93%，一度刷新4月以来新低，从历史高点已经回调了11%，'
        '比三大股指跌幅明显更大，表明资金持续从小盘股中流出。')
    assert result['facts']['gap'] == '873.01'
    assert result['facts']['gap_low_date'] == '2026-10-07'
    assert result['facts']['rut_drawdown'] == '-11.04'
    assert len(result['gap_rows']) >= 60


def test_previous_session_changes_are_recomputed_from_each_fetch():
    """The three index changes must track the fetched closes, never a fixed copy."""
    data = fixtures()
    data['ndx'][-1]['close'] = 27000.0   # +1.35%
    data['spx'][-1]['close'] = 7700.0    # -1.36%
    data['dji'][-1]['close'] = 52049.0   # flat
    result = structure(DAY, data)
    assert result['body'].split('\n')[1].startswith(
        '1、前一个交易日纳斯达克指数收涨1.35%、标普500指数收跌1.36%、道琼斯指数收平，')
    assert result['facts']['ndx_change'] == '1.35'
    assert result['facts']['spx_change'] == '-1.36'
    assert result['facts']['dji_change'] == '0.00'


def test_database_numeric_text_and_new_float_closes_produce_identical_metrics():
    original=fixtures();expected=structure(DAY,original)
    import copy
    restored=copy.deepcopy(original)
    for rows in restored.values():
        for row in rows[:-1]:
            row['close']=str(row['close']);row['low']=str(row['low'])
    assert structure(DAY,restored)==expected


@pytest.mark.parametrize('rut_close,expected', [(2800.0, '收涨'), (2600.0, '收跌')])
def test_small_cap_direction_drives_the_second_item(rut_close, expected):
    data = fixtures()
    data['rut'][-1]['close'] = rut_close
    result = structure(DAY, data)
    assert result['body'].split('\n')[2].startswith('2、代表小盘股的罗素2000指数也' + expected)


def test_chart_stays_inside_delivery_limits(tmp_path):
    result = structure(DAY, fixtures())
    from app.briefs.charts import us_structure_chart
    from app.briefs.delivery import FIXED_NOTES, complete_body
    path = us_structure_chart(result['gap_rows'], tmp_path)
    delivered = compose_image(path, dark=True, notes=FIXED_NOTES['us_structure'])
    content, suffix, media = encode_image(delivered)
    assert len(content) <= MAX_BYTES and media.startswith('image/')
    with Image.open(io.BytesIO(content)) as image:
        assert image.width == 1408 and image.height > 700
        assert image.getpixel((0, 750)) == (0, 0, 0)
    assert complete_body('us_structure', result['body']) == result['body']


def test_structure_edition_puts_notes_on_black_image_footer(db_factory, seed):
    from datetime import date
    from app.briefs.delivery import FIXED_NOTES
    from app.briefs.service import append_version, delivered_image, ensure_issue, issue_out, root_for
    with db_factory() as db:
        issue = ensure_issue(db, seed['editor'].team_id, date(2026, 10, 15), 'us_structure')
        folder = root_for(issue.team_id) / 'runs' / 'structure-note-layout'
        folder.mkdir(parents=True)
        source = folder / '美股市场结构.png'
        Image.new('RGB', (1408, 700), 'black').save(source)
        version = append_version(db, issue, {'title': '美股市场结构',
            'dynamic_body': '当期事实\n\n--------\n\n' + FIXED_NOTES['us_structure'],
            'images': [source.name], 'valid': True, 'data_date': '2026-10-14',
            'image_sha256': {source.name: hashlib.sha256(source.read_bytes()).hexdigest()},
            'artifact_dir': folder.relative_to(root_for(issue.team_id)).as_posix()})
        db.commit()
        shown = issue_out(db, issue)['versions'][0]
        image, _ = delivered_image(issue, version, source.name)
    assert shown['notes_placement'] == 'image' and shown['body'] == '当期事实'
    expected = compose_image(source, dark=True, notes=FIXED_NOTES['us_structure'])
    assert image.read_bytes() == encode_image(expected)[0]
    with Image.open(image) as delivered:
        assert delivered.getpixel((0, 750)) == (0, 0, 0)


def test_fetch_rejects_missing_target_day(tmp_path, monkeypatch):
    data = fixtures()
    broken = {**data, 'spx': [row for row in data['spx'] if row['date'] != DAY]}
    monkeypatch.setattr(fetch_data, 'fetch', lambda: broken)
    with pytest.raises(ValueError, match='目标日期尚未更新'):
        run.fetch(DAY, tmp_path)


def test_render_builds_payload_and_snapshot_rerenders(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_data, 'fetch', lambda: fixtures())
    snapshot = run.fetch(DAY, tmp_path)
    first = run.render(snapshot, tmp_path)
    assert first['title'] == '【美股晴雨表】美股10月14日市场结构：市场宽度依然不佳'
    assert first['data_date'] == DAY and first['images'] == ['美股市场结构.png']
    assert (tmp_path / '美股市场结构.png').is_file()
    again = run.render(snapshot, tmp_path)
    assert again['title'] == first['title'] and again['facts']['gap'] == '873.01'
