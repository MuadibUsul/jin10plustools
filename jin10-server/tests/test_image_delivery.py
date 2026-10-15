import hashlib
import io
import uuid
from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image

from app.briefs.delivery import FIXED_NOTES
from app.briefs.service import append_version, ensure_issue
from app.db.models import BriefConfig, BriefIssue, BriefVersion
from app.services.image_delivery import MAX_BYTES, compose_image, download_name, encode_image
from tests.conftest import login
from tests.test_briefs import draft


@pytest.mark.parametrize('category',['volume','turnover','valuation','liquidity'])
def test_real_charts_have_bounded_readable_deliveries(category):
    source=next((Path('app/briefs/samples')/category).glob('*.png'))
    notes=FIXED_NOTES['valuation'] if category=='valuation' else ''
    image=compose_image(source,notes=notes,dark=category in ('valuation','turnover'),
                        remove_valuation_source=category=='valuation',reuse_footer=category=='liquidity')
    content,suffix,media=encode_image(image)
    assert len(content)<=MAX_BYTES
    with Image.open(io.BytesIO(content)) as saved:
        assert saved.width==image.width  # These chart fixtures must not need shrinking.
        assert saved.height==image.height
        with Image.open(source) as original:
            assert saved.height>=original.height
        assert suffix in ('.png','.jpg') and media.startswith('image/')
    if category=='valuation':
        assert image.getpixel((100,565))==(0,0,0)
        assert image.height>1400  # Full notes are laid out below both charts.


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
    assert versions[1]['fixed_notes']!=versions[0]['fixed_notes']
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
