import hashlib
import io
import json
import uuid
from pathlib import Path
from urllib.parse import unquote

import pytest
from sqlalchemy import select
from PIL import Image, ImageChops

from app.briefs.delivery import FIXED_NOTES
from app.briefs.charts import VALUATION_PLOT_BOUNDS, valuation_chart
from app.briefs.service import append_version, ensure_issue
from app.db.models import BriefConfig, BriefIssue, BriefVersion
from app.services.image_delivery import MAX_BYTES, compose_image, download_name, encode_image
from tests.conftest import login
from tests.test_briefs import draft


def test_turnover_new_and_archived_images_have_one_standard_watermark(db_factory, seed):
    from datetime import date, timedelta
    import numpy as np
    from app.briefs.engines.turnover.build_turnover import load_and_calculate
    from app.briefs.engines.turnover.render_template import render_template, HERE
    from app.briefs.service import root_for, delivered_image
    from app.services.image_delivery import WATERMARK

    with db_factory() as db:
        issue = ensure_issue(db, seed['editor'].team_id, date(2026, 10, 15), 'turnover')
        folder = root_for(issue.team_id) / 'runs' / 'turnover-regression'
        folder.mkdir(parents=True)
        # A 2% high-turnover series passes through the old logo region. Its curve
        # must be redrawn from captured counts rather than erased with the logo.
        raw = [{'date': (date(2026, 9, 15) + timedelta(days=i)).isoformat(),
                'totalCompany': 100, 'belowPercent3': 70, 'belowPercent5': 98,
                'close': 3000 + i} for i in range(30)]
        snapshot = folder / 'source.json'
        snapshot.write_text(json.dumps(raw), encoding='utf-8')
        chart, _, _, audit = render_template(load_and_calculate(snapshot), folder)
        assert audit['static_pixels_changed'] == 0
        with Image.open(chart) as opened:
            clean = opened.convert('RGB')
        region = np.asarray(clean)[1318:1361, 765:1068].astype(int)
        purple = ((region[:, :, 2] > 60) & (region[:, :, 2] > region[:, :, 0] * 1.6)
                  & (region[:, :, 2] > region[:, :, 1] * 1.3))
        assert not purple.any()
        curve = np.asarray(clean)[1325:1331, 800:950]
        assert ((curve[:, :, 1] > 160) & (curve[:, :, 0] < 80)).sum() > 100

        # Simulate an immutable old edition that embeds the supplied template logo.
        legacy = clean.copy()
        with Image.open(HERE / 'assets/turnover_template.png') as template:
            legacy.paste(template.crop((765, 1318, 1068, 1361)), (765, 1318))
        legacy.save(chart)
        original_bytes = chart.read_bytes()
        version = append_version(db, issue, {'title': '换手率测试', 'dynamic_body': '已核验事实',
            'data_date': raw[-1]['date'], 'valid': True, 'images': [chart.name],
            'image_sha256': {chart.name: hashlib.sha256(original_bytes).hexdigest()},
            'artifact_dir': folder.relative_to(root_for(issue.team_id)).as_posix()})
        db.commit()
        rebuilt = compose_image(chart, dark=True, turnover_snapshot=snapshot)
        assert ImageChops.difference(clean, rebuilt.crop((0, 0, *clean.size))).getbbox() is None
        with Image.open(WATERMARK) as asset:
            logo = asset.convert('RGBA')
        margin = round(clean.width * .03)
        logo_width = round(clean.width * .23)
        logo = logo.resize((logo_width, round(logo.height * logo_width / logo.width)), Image.Resampling.LANCZOS)
        expected_logo = Image.new('RGB', logo.size, 'black')
        expected_logo.paste(logo, (0, 0), logo)
        x, y = rebuilt.width - margin - logo.width, rebuilt.height - margin - logo.height
        assert ImageChops.difference(expected_logo, rebuilt.crop((x, y, x + logo.width, y + logo.height))).getbbox() is None
        path, _ = delivered_image(issue, version, chart.name)
        # The delivered turnover image now carries the fixed notes band under the chart.
        expected = compose_image(chart, notes=FIXED_NOTES['turnover'], dark=True, turnover_snapshot=snapshot)
        assert path.read_bytes() == encode_image(expected)[0]
        assert path.stat().st_size <= MAX_BYTES
        stamp = path.stat().st_mtime_ns
        assert delivered_image(issue, version, chart.name)[0].stat().st_mtime_ns == stamp
        assert chart.read_bytes() == original_bytes


@pytest.mark.parametrize('category',['volume','turnover','valuation','liquidity'])
def test_real_charts_have_bounded_readable_deliveries(category):
    source=next((Path('app/briefs/samples')/category).glob('*.png'))
    notes=FIXED_NOTES[category] if category in ('valuation','turnover') else ''
    image=compose_image(source,notes=notes,dark=category in ('valuation','turnover'),
                        remove_valuation_source=category=='valuation',reuse_footer=category=='liquidity')
    content,suffix,media=encode_image(image)
    assert len(content)<=MAX_BYTES
    with Image.open(io.BytesIO(content)) as saved:
        assert saved.width==image.width  # These chart fixtures must not need shrinking.
        assert saved.height==image.height
        with Image.open(source) as original:
            assert saved.height>=original.height
            if category=='turnover':
                # The note block sits under the chart without stretching the page.
                assert original.height+150<image.height<=original.height+420
        assert suffix in ('.png','.jpg') and media.startswith('image/')
    if category=='valuation':
        assert image.getpixel((100,565))==(0,0,0)
        assert image.height>1400  # Full notes are laid out below both charts.


def test_turnover_notes_move_onto_the_image_and_leave_the_body(client,db_factory,seed):
    from datetime import date
    from app.briefs.service import root_for, issue_out
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,17),'turnover')
        folder=root_for(issue.team_id)/'runs/turnover-notes';folder.mkdir(parents=True)
        Image.new('RGB',(1118,1407),'black').save(folder/'chart.png')
        # An earlier edition stored the fixed notes appended to its body.
        stored='当日换手率事实\n\n--------\n\n'+FIXED_NOTES['turnover']
        append_version(db,issue,{'title':'换手率','dynamic_body':stored,'valid':True,
            'images':['chart.png'],'image_sha256':{'chart.png':hashlib.sha256((folder/'chart.png').read_bytes()).hexdigest()},
            'artifact_dir':'runs/turnover-notes','data_date':'2026-10-16',
            'turnover_watermark_removed':True})
        db.commit();issue_id=issue.id
        first=issue_out(db,issue)['versions'][0]
    assert first['notes_placement']=='image'
    assert first['body']=='当日换手率事实' and first['dynamic_body']=='当日换手率事实'
    assert first['fixed_notes']==FIXED_NOTES['turnover']
    headers=login(client,'editor@plus.test')
    image=client.get(first['images'][0]['url'],headers=headers)
    assert image.status_code==200 and len(image.content)<=MAX_BYTES
    with Image.open(io.BytesIO(image.content)) as delivered:
        assert delivered.width==1118 and delivered.height>1407
    # A manual edit keeps the notes out of the body as well.
    response=client.post(f'/api/briefs/issues/{issue_id}/revisions',headers=headers,json={
        'base_version':1,'title':'换手率','dynamic_body':'编辑后的动态事实','fixed_notes':'注：自定义换手率说明。'})
    assert response.status_code==201
    latest=response.json()['versions'][0]
    assert latest['body']=='编辑后的动态事实' and latest['notes_placement']=='image'
    assert latest['fixed_notes']=='注：自定义换手率说明。'


def test_notes_are_versioned_and_defaults_apply_to_future_issues(client,db_factory,seed):
    id=draft(db_factory,seed)
    headers=login(client,'editor@plus.test')
    request={'base_version':1,'title':'更新标题','dynamic_body':'动态事实',
             'fixed_notes':'注：自定义说明。','notes_as_default':True}
    response=client.post(f'/api/briefs/issues/{id}/revisions',headers=headers,json=request)
    assert response.status_code==201
    versions=response.json()['versions']
    assert versions[0]['fixed_notes']=='注：自定义说明。'
    assert '自定义说明' in versions[0]['body']
    assert len(versions)==1
    with db_factory() as db:
        previous=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==uuid.UUID(id),BriefVersion.number==1))
        assert previous.payload['fixed_notes']!=versions[0]['fixed_notes']
    image=client.get(versions[0]['images'][0]['url'],headers=headers)
    assert image.status_code==200 and len(image.content)<=MAX_BYTES
    assert download_name('2026-10-15','更新标题','.png') in unquote(image.headers['content-disposition'])
    with db_factory() as db:
        config=db.get(BriefConfig,seed['editor'].team_id)
        assert config.notes['liquidity']=='注：自定义说明。'
        assert not config.enabled
        from datetime import date
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,16),'liquidity')
        old=db.get(BriefIssue,uuid.UUID(id))
        payload=dict(db.query(BriefVersion).filter_by(issue_id=old.id,number=1).one().payload)
        payload.pop('fixed_notes')
        new=append_version(db,issue,payload)
        assert new.payload['fixed_notes']=='注：自定义说明。'


def test_valuation_note_edit_changes_image_without_changing_prior_edition(client,db_factory,seed):
    from datetime import date
    from app.briefs.service import root_for, issue_out
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,15),'valuation')
        folder=root_for(issue.team_id)/'runs/valuation';folder.mkdir(parents=True)
        Image.new('RGB',(1408,1190),'black').save(folder/'chart.png')
        append_version(db,issue,{'title':'估值','dynamic_body':'当日事实','valid':True,
            'images':['chart.png'],'image_sha256':{'chart.png':hashlib.sha256((folder/'chart.png').read_bytes()).hexdigest()},
            'artifact_dir':'runs/valuation','data_date':'2026-10-14'})
        db.commit();id=issue.id
        first=issue_out(db,issue)['versions'][0]
    headers=login(client,'editor@plus.test')
    before=client.get(first['images'][0]['url'],headers=headers).content
    response=client.post(f'/api/briefs/issues/{id}/revisions',headers=headers,json={
        'base_version':1,'title':'估值','dynamic_body':'当日事实','fixed_notes':'注：修改后的解释。'})
    latest=response.json()['versions'][0]
    assert latest['body']=='当日事实' and latest['notes_placement']=='image'
    after=client.get(latest['images'][0]['url'],headers=headers).content
    assert before!=after and len(after)<=MAX_BYTES
    assert client.get(first['images'][0]['url'],headers=headers).content==before


def test_unified_valuation_panels_share_plot_geometry_and_stay_legible(tmp_path):
    (first,second)=VALUATION_PLOT_BOUNDS
    assert first[0]==second[0] and first[2]==second[2]
    assert first[3]-first[1]==second[3]-second[1]
    seed=Path('app/briefs/seed')
    chart=valuation_chart(json.loads((seed/'buffett-history.json').read_text()),
                          json.loads((seed/'erp-history.json').read_text()),tmp_path)
    with Image.open(chart) as raw:
        # The three upper reference levels are part of the chart, not labels
        # supplied by the original workbook image.
        for y,dominant in ((251,0),(396,0),(493,1)):
            pixels=raw.crop((100,y-2,1300,y+3)).getdata()
            assert sum(pixel[dominant]>120 and pixel[dominant]>pixel[2]
                       for pixel in pixels)>100
        # The bundled primary subset lacks 极; the green first character must
        # still be visible through its licensed fallback glyph.
        first_glyph=raw.crop((1250,458,1266,485)).getdata()
        assert sum(pixel[1]>110 and pixel[1]>pixel[0]+20 for pixel in first_glyph)>20
    composed=compose_image(chart,notes=FIXED_NOTES['valuation'],dark=True)
    content,suffix,_=encode_image(composed)
    assert suffix=='.png' and len(content)<=MAX_BYTES
    with Image.open(io.BytesIO(content)) as image:
        assert image.width==1408
        assert image.getpixel((first[0],first[1]))!=(0,0,0)
        assert image.getpixel((second[0],second[1]))!=(0,0,0)


def test_erp_panel_fits_the_index_axis_to_its_observed_range():
    from app.briefs.charts import erp_index_limits
    # 500-point ticks, a round bottom with >=250 points of headroom, 6,000 top floor.
    assert erp_index_limits([1000, 4200, 5800]) == (500, 6000)
    assert erp_index_limits([2853.75, 5807.72]) == (2500, 6000)
    assert erp_index_limits([1490, 5800]) == (1000, 6000)
    assert erp_index_limits([100, 5800]) == (0, 6000)
    assert erp_index_limits([6200, 6900]) == (5500, 7000)
    for sample in ([1000, 4200], [1490, 5800], [2853.75, 5807.72]):
        low, high = erp_index_limits(sample)
        assert low % 500 == 0 and high % 500 == 0 and low < min(sample) and high >= max(sample)


def test_notes_font_fallback_covers_every_editable_character():
    from app.services.image_delivery import NoteFonts
    fonts = NoteFonts(20)
    if fonts.fallback is None:
        pytest.skip('未安装系统 CJK 字体，无法检查缺字回退')
    missing = {char for text in FIXED_NOTES.values() for char in text.replace('\n', '')
               if not fonts.covered(char)}
    assert not missing, f'注释存在无法渲染的字符：{sorted(missing)}'
