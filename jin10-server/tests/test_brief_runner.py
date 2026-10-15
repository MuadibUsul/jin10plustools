import hashlib
from pathlib import Path
from unittest.mock import patch
import pytest

from app.briefs.runner import generate
from app.briefs.sources import save,normalize_volume
from app.briefs.delivery import complete_body,FIXED_NOTES,OIL_CLOSING
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


def test_fixed_explanations_are_identical_and_not_duplicated():
    for category,note in FIXED_NOTES.items():
        body=complete_body(category,'当日事实。')
        assert body.endswith(note);assert complete_body(category,body)==body
    assert complete_body('oil_etf',complete_body('oil_etf','期货事实。')).count(OIL_CLOSING)==1


def test_incomplete_volume_is_not_a_current_day_observation():
    with pytest.raises(ValueError):normalize_volume({'data':[]},'2026-10-14')
    with pytest.raises(ValueError):normalize_volume({'data':[{'date':'2026-10-14','sh_turn':1,'sz_turn':None,'sh_vol':1,'sz_vol':1}]},'2026-10-14')
