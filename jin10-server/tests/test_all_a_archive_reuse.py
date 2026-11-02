import hashlib,json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from app.briefs.engines.valuation import fetch_free_all_a as cap
from app.briefs.source_archive import archive_run,restore_all_a_capture
from app.db.models import BriefJob,BriefSourceSnapshot


def capture(folder):
    folder.mkdir(parents=True)
    rows=[]
    for day in ('2026-10-29','2026-10-30'):
        for code,exchange in [('600000','SH'),('000001','SZ'),('920001','BJ')]:
            rows.append({'TRADE_DATE':day+' 00:00:00','SECURITY_CODE':code,'SECUCODE':code+'.'+exchange,
                'TOTAL_MARKET_CAP':1000,'CLOSE_PRICE':10,'TOTAL_SHARES':100})
    raw=json.dumps({'success':True,'code':0,'result':{'pages':1,'count':len(rows),'data':rows}}).encode()
    source=folder/'all-a-source-page1.json';source.write_bytes(raw)
    normalized=cap.normalize_all_a([raw],'2026-10-30','2026-10-29')
    normalized['raw_evidence']=[{'file':str(source.resolve()),'sha256':hashlib.sha256(raw).hexdigest()}]
    path=folder/'all-a-source-normalized.json';path.write_text(json.dumps(normalized),encoding='utf-8')
    return path


def test_only_exact_date_complete_team_archives_are_restored(db_factory,seed,tmp_path):
    folder=tmp_path/'old'/'all-a';capture(folder)
    with db_factory() as db:
        old=BriefJob(team_id=seed['editor'].team_id,category='valuation',issue_date=date(2026,11,2),
            dedupe_key='old-market-cap',attempts=1)
        db.add(old);db.flush();archive_run(db,old,folder.parent)
        job=type('Job',(),{'team_id':seed['editor'].team_id})()
        other=type('Job',(),{'team_id':seed['outsider'].team_id})()
        assert not restore_all_a_capture(db,other,tmp_path/'other','2026-10-30','2026-10-29')
        assert not restore_all_a_capture(db,job,tmp_path/'wrong','2026-10-29','2026-10-28')
        assert restore_all_a_capture(db,job,tmp_path/'restored','2026-10-30','2026-10-29')
        result=cap.load_verified_capture(tmp_path/'restored'/'all-a-restored.json','2026-10-30','2026-10-29')
        assert result['restored_from_database'] and result['source_snapshot_id']
        assert result['rows'][-1]['market_cap_cny']=='3000'
        source=db.scalar(select(BriefSourceSnapshot).where(BriefSourceSnapshot.relative_path.endswith('page1.json')))
        db.delete(source);db.flush()
        assert not restore_all_a_capture(db,job,tmp_path/'incomplete','2026-10-30','2026-10-29')


def test_transport_failure_can_reuse_verified_pages_and_keeps_warning(tmp_path,monkeypatch):
    path=capture(tmp_path/'archive')
    def unavailable(*a,**kw):raise cap.FetchAllAError('network_error','timed out')
    monkeypatch.setattr(cap,'fetch_all_a',unavailable)
    restored=cap.fetch_with_archive('2026-10-30','2026-10-29',tmp_path/'fresh',path)
    assert restored['acquisition_warning'] and restored['network_error']=='timed out'
    raw_path=Path(json.loads(path.read_text())['raw_evidence'][0]['file'])
    raw_path.write_bytes(b'changed')
    with pytest.raises(cap.FetchAllAError,match='digest'):
        cap.fetch_with_archive('2026-10-30','2026-10-29',tmp_path/'fresh',path)


@pytest.mark.parametrize('code',['source_changed','http_error','incomplete_data'])
def test_source_or_access_rejection_is_not_hidden_by_an_archive(tmp_path,monkeypatch,code):
    path=capture(tmp_path/'archive')
    def invalid(*a,**kw):raise cap.FetchAllAError(code,'rejected')
    monkeypatch.setattr(cap,'fetch_all_a',invalid)
    with pytest.raises(cap.FetchAllAError) as exc:
        cap.fetch_with_archive('2026-10-30','2026-10-29',tmp_path/'fresh',path)
    assert exc.value.code==code


def test_modified_aggregate_cannot_override_the_original_page(tmp_path):
    path=capture(tmp_path/'archive');data=json.loads(path.read_text())
    data['rows'][-1]['market_cap_yi']='999';path.write_text(json.dumps(data))
    with pytest.raises(cap.FetchAllAError,match='aggregate'):
        cap.load_verified_capture(path,'2026-10-30','2026-10-29')
