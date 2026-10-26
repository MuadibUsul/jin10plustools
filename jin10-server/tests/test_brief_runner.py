import hashlib
from pathlib import Path
from unittest.mock import patch
import pytest

from app.briefs.runner import generate, pipeline_signature
from app.briefs.sources import save,normalize_volume
from app.briefs.delivery import IMAGE_NOTE_CATEGORIES,complete_body,FIXED_NOTES,OIL_CLOSING
from app.briefs.policy import production_window,CHINA
from datetime import datetime


def test_ten_oclock_cutoff_keeps_nine_thirty_inside_window():
    assert production_window(datetime(2026,10,15,9,30,tzinfo=CHINA))
    assert production_window(datetime(2026,10,15,9,59,59,tzinfo=CHINA))
    assert not production_window(datetime(2026,10,15,10,0,tzinfo=CHINA))


def test_cached_rules_reuse_sources_and_render_repairs_without_fetch(tmp_path):
    def payload(out):
        (out/'image.png').write_bytes(b'valid-chart')
        return {'title':'核验标题','dynamic_body':'核验正文。','data_date':'2026-10-14','images':['image.png'],'warnings':[],'facts':{}}
    def source(day,out,state,refresh=False):
        save(out/'plot-data.json',[{'date':day}]);return payload(out)
    with patch.dict('app.briefs.runner.ADAPTERS',{'volume':source}), \
         patch('app.briefs.engines.liquidity.copy_tracks.generate_deepseek',return_value={'status':'not_configured'}) as ai:
        first=generate('volume','2026-10-14',tmp_path,'first')
        with patch.dict('app.briefs.runner.ADAPTERS',{'volume':lambda *a,**k:pytest.fail('unexpected external fetch')}):
            second=generate('volume','2026-10-14',tmp_path,'second')
            assert second==first;assert ai.call_count==1
            image=tmp_path/first['artifact_dir']/'image.png';image.write_bytes(b'corrupt')
            with pytest.raises(ValueError,match='缓存图片损坏'):generate('volume','2026-10-14',tmp_path,'bad')
            with patch('app.briefs.sources.render_volume',side_effect=lambda rows,out:payload(out)):
                restored=generate('volume','2026-10-14',tmp_path,'restored','render')
            assert (tmp_path/restored['artifact_dir']/'image.png').read_bytes()==b'valid-chart'
            assert ai.call_count==1


def test_manual_produce_refreshes_and_accepts_the_newest_available_data_date(tmp_path, monkeypatch):
    calls=[]
    def source(day,out,state,refresh=False):
        calls.append(day)
        (out/'image.png').write_bytes(b'valid-chart')
        return {'title':'最新数据','dynamic_body':'截至最新数据日。','data_date':'2026-10-14','images':['image.png'],'warnings':[],'facts':{}}
    monkeypatch.setenv('MANUAL_GENERATION','1')
    monkeypatch.setenv('SKILL_PRODUCTION','1')
    with patch.dict('app.briefs.runner.ADAPTERS',{'token_cost':source}):
        first=generate('token_cost','2026-10-20',tmp_path,'manual-first')
        second=generate('token_cost','2026-10-20',tmp_path,'manual-second')
    assert first['data_date']==second['data_date']=='2026-10-14'
    assert len(calls)==2
    monkeypatch.setenv('MANUAL_GENERATION','0')
    with patch.dict('app.briefs.runner.ADAPTERS',{'token_cost':source}):
        with pytest.raises(ValueError,match='缓存日期不符'):
            generate('token_cost','2026-10-20',tmp_path,'automatic')


def test_fixed_explanations_are_identical_and_not_duplicated():
    for category,note in FIXED_NOTES.items():
        body=complete_body(category,'当日事实。')
        # Image-note categories keep the explanatory block off the dynamic body.
        assert body=='当日事实。' if category in IMAGE_NOTE_CATEGORIES else body.endswith(note)
        assert complete_body(category,body)==body
    assert complete_body('oil_etf',complete_body('oil_etf','期货事实。')).count(OIL_CLOSING)==1
    # The oil closing note continues the last sentence and never starts its own line.
    assert complete_body('oil_etf','期货事实。')=='期货事实。'+OIL_CLOSING
    assert complete_body('oil_etf','期货事实。\n\n'+OIL_CLOSING)=='期货事实。'+OIL_CLOSING


def test_incomplete_volume_is_not_a_current_day_observation():
    with pytest.raises(ValueError):normalize_volume({'data':[]},'2026-10-14')
    with pytest.raises(ValueError):normalize_volume({'data':[{'date':'2026-10-14','sh_turn':1,'sz_turn':None,'sh_vol':1,'sz_vol':1}]},'2026-10-14')


def test_liquidity_uses_final_required_title(tmp_path):
    from app.briefs.sources import render_liquidity
    snapshot={'quotes':{'quote_date':'2026-10-15'},'history':{'start':'2026-07-15','end':'2026-10-15'}}
    with patch('app.briefs.engines.liquidity.render.render_poster',return_value=(tmp_path/'poster.png',None,{})), \
         patch('app.briefs.engines.liquidity.copy_tracks.build_copy',return_value={'title':'旧标题','body':'当日事实。'}):
        result=render_liquidity(snapshot,tmp_path)
    assert result['title']=='【流动性指标】质押式回购利率走势与最新行情'


def test_cached_valuation_copy_uses_a_share_label(tmp_path):
    source=tmp_path/'runs/original';source.mkdir(parents=True)
    (source/'chart.png').write_bytes(b'chart')
    body='巴菲特指数（全A总市值/上年度全年GDP）为87.21%。'
    save(tmp_path/'cache/valuation/2026-10-14.json',{
        'title':'估值','dynamic_body':body,'data_date':'2026-10-14',
        'images':['chart.png'],'image_sha256':{'chart.png':hashlib.sha256(b'chart').hexdigest()},
        'artifact_dir':'runs/original','deepseek':{'status':'ok','body':body},
        'pipeline':pipeline_signature()})
    with patch.dict('app.briefs.runner.ADAPTERS',{'valuation':lambda *a,**k:pytest.fail('unexpected fetch')}):
        result=generate('valuation','2026-10-14',tmp_path,'updated')
    assert result['dynamic_body']=='巴菲特指数（A股总市值/上年度全年GDP）为87.21%。'
    assert result['deepseek']['body']==result['dynamic_body']


def test_default_notes_change_prevents_cache_reuse(tmp_path, monkeypatch):
    source=tmp_path/'runs/original';source.mkdir(parents=True)
    (source/'image.png').write_bytes(b'old-image')
    save(source/'plot-data.json',[{'date':'2026-10-14'}])
    save(tmp_path/'cache/volume/2026-10-14.json',{
        'title':'标题','dynamic_body':'正文','data_date':'2026-10-14','fixed_notes':'旧注释',
        'images':['image.png'],'image_sha256':{'image.png':hashlib.sha256(b'old-image').hexdigest()},
        'artifact_dir':'runs/original','deepseek':{'status':'not_configured'},
        'pipeline':pipeline_signature()})
    monkeypatch.setenv('CATEGORY_NOTES','新注释')
    def rendered(rows,out):
        (out/'image.png').write_bytes(b'new-image')
        return {'title':'标题','dynamic_body':'正文','data_date':'2026-10-14','images':['image.png'],'facts':{},'warnings':[]}
    with patch.dict('app.briefs.runner.ADAPTERS',{'volume':lambda *a,**k:pytest.fail('unexpected fetch')}),          patch('app.briefs.sources.render_volume',rendered):
        result=generate('volume','2026-10-14',tmp_path,'notes')
    assert (tmp_path/result['artifact_dir']/'image.png').read_bytes()==b'new-image'
