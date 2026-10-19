import hashlib
import io
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from PIL import Image

from app.briefs.delivery import FIXED_NOTES, complete_body
from app.briefs.engines.us_barometer import fetch_data
from app.briefs.engines.us_barometer import run
from app.briefs.engines.us_barometer.calculate import buffett_series, erp_series, grade, BUFFETT_GRADES, ERP_GRADES, verdict_copy
from app.services.image_delivery import MAX_BYTES, compose_image, encode_image


def fixtures():
    day = date(2026, 10, 14)
    wilshire = [{'date': (day - timedelta(days=offset)).isoformat(),
                 'close': 76581.5234375 if offset == 0 else 75000 + offset} for offset in range(20, -1, -1)]
    gdp = [{'period': '2026-01-01', 'value': 31906.274}, {'period': '2026-04-01', 'value': 32563.03}]
    erp = [{'date': row['date'], 'erp': 0.0533 if row['date'] == day.isoformat() else 0.05} for row in wilshire]
    return day, wilshire, gdp, erp


def test_sample_reproduces_the_approved_numbers():
    day, wilshire, gdp, erp = fixtures()
    series = buffett_series(wilshire, gdp)
    today = next(row for row in series if row['date'] == day.isoformat())
    assert today['pct'] == Decimal('235.18')
    assert grade(today['pct'], BUFFETT_GRADES)['label'] == '严重高估'
    erp_today = next(row for row in erp_series(erp) if row['date'] == day.isoformat())
    assert erp_today['pct'] == Decimal('5.33')
    assert grade(erp_today['pct'], ERP_GRADES)['label'] == '偏低'
    copy = verdict_copy(day, today['pct'], erp_today['pct'])
    assert copy['title'] == '【美股晴雨表】美股10月14日投资性价比：较低'
    assert copy['body'] == (
        '1、巴菲特指数：235.18%，处于严重高估水平，股市表现已经超过合理区间，需要有进一步高增长业绩出现才能支撑当前估值。\n'
        '2、美国标普500指数股权风险溢价（ERP）：5.33%，处于偏低水平，暗示目前股市相对国债收益并无优势。')
    assert copy['label'] == '较低' and copy['score'] == -3


@pytest.mark.parametrize('buffett,erp,verdict', [
    (90, 7.2, '较高'), (60, 7.2, '高'), (120, 5.6, '中性'), (160, 6.5, '中性'),
    (250, 4.4, '低'), (90, 4.4, '中性'), (235.18, 5.33, '较低'),
])
def test_overall_verdict_uses_both_grades(buffett, erp, verdict):
    assert verdict_copy('2026-10-14', Decimal(str(buffett)), Decimal(str(erp)))['label'] == verdict


def test_merged_chart_stays_inside_the_delivery_limits(tmp_path):
    day, wilshire, gdp, erp = fixtures()
    buffett = buffett_series(wilshire, gdp)
    series = erp_series(erp)
    from app.briefs.charts import us_barometer_chart
    path = us_barometer_chart(buffett, series, tmp_path)
    assert path.name == '美股晴雨表.png'
    delivered = compose_image(path, dark=True, notes=FIXED_NOTES['us_barometer'])
    content, suffix, media = encode_image(delivered)
    assert len(content) <= MAX_BYTES and media.startswith('image/')
    with Image.open(io.BytesIO(content)) as image:
        assert image.width == 1408 and image.height > 1450
    with Image.open(path) as chart:
        assert chart.height == 1190


def test_fetch_rejects_a_missing_target_day(tmp_path, monkeypatch):
    day, wilshire, gdp, erp = fixtures()
    monkeypatch.setattr(fetch_data, 'wilshire', lambda: [row for row in wilshire if row['date'] != day.isoformat()])
    monkeypatch.setattr(fetch_data, 'gdp', lambda: gdp)
    monkeypatch.setattr(fetch_data, 'streetstats_erp', lambda: erp)
    with pytest.raises(ValueError, match='目标日期尚未更新'):
        run.fetch(day.isoformat(), tmp_path)


def test_render_builds_payload_and_snapshot_rerenders(tmp_path, monkeypatch):
    day, wilshire, gdp, erp = fixtures()
    monkeypatch.setattr(fetch_data, 'wilshire', lambda: wilshire)
    monkeypatch.setattr(fetch_data, 'gdp', lambda: gdp)
    monkeypatch.setattr(fetch_data, 'streetstats_erp', lambda: erp)
    snapshot = run.fetch(day.isoformat(), tmp_path)
    first = run.render(snapshot, tmp_path)
    assert first['title'] == '【美股晴雨表】美股10月14日投资性价比：较低'
    assert first['data_date'] == day.isoformat()
    assert first['images'] == ['美股晴雨表.png']
    assert all((tmp_path / name).is_file() for name in first['images'])
    assert first['facts']['buffett_pct'] == '235.18' and first['facts']['erp_pct'] == '5.33'
    assert first['facts']['gdp_period'] == '2026-04-01'
    # Notes live on the delivered image now, so the body keeps only dynamic facts.
    body = complete_body('us_barometer', first['dynamic_body'])
    assert body == first['dynamic_body'] and FIXED_NOTES['us_barometer'] not in body
    again = run.render(snapshot, tmp_path)
    assert again['title'] == first['title'] and again['images'] == first['images']


def test_stale_cached_product_is_rebuilt_from_its_snapshot(tmp_path):
    """A cache produced by older chart/copy rules must never be served unchanged."""
    from app.briefs import runner
    day, wilshire, gdp, erp = fixtures()
    snapshot = {'target': day.isoformat(), 'wilshire': run.window(wilshire, day.isoformat()),
                'gdp': gdp, 'erp': run.window(erp, day.isoformat()), 'sources': dict(run.SOURCES)}
    artifact = tmp_path / 'runs' / 'old'
    artifact.mkdir(parents=True)
    (artifact / 'us-snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
    old_images = ['美股巴菲特指数.png', '美股股权风险溢价.png']
    hashes = {}
    for name in old_images:
        (artifact / name).write_bytes(b'old chart')
        hashes[name] = hashlib.sha256(b'old chart').hexdigest()
    cache = tmp_path / 'cache' / 'us_barometer' / '2026-10-14.json'
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({
        'title': '【美股晴雨表】美股10月14日投资性价比：较低',
        'dynamic_body': '1、巴菲特指数：235.18%，处于严重高估水平（图）。\n2、股权风险溢价（图）。',
        'images': old_images, 'data_date': day.isoformat(), 'artifact_dir': 'runs/old',
        'image_sha256': hashes, 'deepseek': {'status': 'not_configured'},
        'facts': {}, 'warnings': [],
    }, ensure_ascii=False), encoding='utf-8')

    payload = runner.generate('us_barometer', day.isoformat(), tmp_path, 'new-run')

    assert payload['images'] == ['美股晴雨表.png']
    assert '（图）' not in payload['dynamic_body']
    assert payload['pipeline'] == runner.pipeline_signature()
    assert (tmp_path / payload['artifact_dir'] / '美股晴雨表.png').is_file()
    # Same pipeline again: the freshly produced product is reused as-is.
    again = runner.generate('us_barometer', day.isoformat(), tmp_path, 'second-run')
    assert again['artifact_dir'] == payload['artifact_dir']
    assert again['images'] == payload['images']


def test_notes_render_on_the_image_and_leave_the_body(client, db_factory, seed):
    import hashlib
    from datetime import date
    from app.briefs.service import append_version, ensure_issue, issue_out, root_for
    from tests.conftest import login
    with db_factory() as db:
        issue = ensure_issue(db, seed['editor'].team_id, date(2026, 10, 17), 'us_barometer')
        folder = root_for(issue.team_id) / 'runs' / 'us-barometer-notes'
        folder.mkdir(parents=True)
        Image.new('RGB', (1408, 1190), 'black').save(folder / '美股晴雨表.png')
        Image.new('RGB', (1408, 700), 'black').save(folder / '美股巴菲特指数.png')
        append_version(db, issue, {'title': '美股晴雨表', 'dynamic_body': '当日事实', 'valid': True,
                                   'images': ['美股晴雨表.png', '美股巴菲特指数.png'],
                                   'image_sha256': {name: hashlib.sha256(
                                       (folder / name).read_bytes()).hexdigest()
                                       for name in ('美股晴雨表.png', '美股巴菲特指数.png')},
                                   'artifact_dir': folder.relative_to(root_for(issue.team_id)).as_posix(),
                                   'data_date': '2026-10-16'})
        db.commit()
        first = issue_out(db, issue)['versions'][0]
    assert first['notes_placement'] == 'image'
    assert first['body'] == '当日事实' and first['dynamic_body'] == '当日事实'
    assert first['fixed_notes'] == FIXED_NOTES['us_barometer']
    headers = login(client, 'editor@plus.test')
    image = client.get(first['images'][0]['url'], headers=headers)
    assert image.status_code == 200 and len(image.content) <= MAX_BYTES
    with Image.open(io.BytesIO(image.content)) as delivered:
        assert delivered.width == 1408 and delivered.height > 1190
    # Legacy two-image editions only get the watermark band, never the note band.
    legacy = client.get(first['images'][1]['url'], headers=headers)
    with Image.open(io.BytesIO(legacy.content)) as delivered:
        assert 700 <= delivered.height <= 900
