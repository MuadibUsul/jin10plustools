import hashlib
import json
from datetime import date

import pytest
from sqlalchemy import select

from app.briefs.data_reliability import coverage
from app.briefs.engines.oil_etf.previous import parse
from app.briefs.repair import recover_oil_futures,restore_working_history
from app.db.models import BriefJob,BriefSourceSnapshot,BriefDataPoint


def archive(db,user):
    job=BriefJob(team_id=user.team_id,category='oil_etf',issue_date=date(2026,10,14),
        action='produce',status='succeeded',attempts=1,created_by=user.id,dedupe_key='existing-oil-test')
    db.add(job);db.flush()
    funds={}
    for ticker,name,contract,quantity in [('USO','WTI CRUDE FUTURE','CLZ6',16325),
                                         ('BNO','BRENT CRUDE FUTR','COZ6',6962)]:
        raw=(f'Etf holdings as of <span>October 13, 2026</span>'
             f'<table><tr><td>1</td><td>{name}</td><td>{contract}</td>'
             f'<td>USD</td><td>{quantity}</td></tr></table>').encode()
        funds[ticker]=parse(ticker,raw,'2026-10-13')
        db.add(BriefSourceSnapshot(team_id=user.team_id,job_id=job.id,attempt=1,category='oil_etf',
            data_date=date(2026,10,13),relative_path=f'history/source-archive/2026-10-13/{ticker}.html',
            content=raw,sha256=hashlib.sha256(raw).hexdigest(),byte_count=len(raw)))
    raw=json.dumps({'data_date':'2026-10-13','funds':funds}).encode()
    db.add(BriefSourceSnapshot(team_id=user.team_id,job_id=job.id,attempt=1,category='oil_etf',
        data_date=date(2026,10,13),relative_path='history/source-archive/2026-10-13/manifest.json',
        content=raw,sha256=hashlib.sha256(raw).hexdigest(),byte_count=len(raw)))
    db.flush()


def test_existing_archive_restores_futures_without_inventing_uso_swaps(db_factory,seed,tmp_path):
    user=seed['editor']
    with db_factory() as db:
        archive(db,user)
        assert recover_oil_futures(db,seed['outsider'].team_id)==0
        assert recover_oil_futures(db,user.team_id)==2
        assert recover_oil_futures(db,user.team_id)==0
        rows={r.series:r for r in db.scalars(select(BriefDataPoint))}
        assert set(rows)=={'BNO','USO_futures'}
        assert rows['BNO'].values['barrels']=='6962000'
        assert rows['USO_futures'].values['futures_barrels']=='16325000'
        assert 'barrels' not in rows['USO_futures'].values
        assert all(r.source_snapshot_id for r in rows.values())
        report=coverage(db,user.team_id,'oil_etf','2026-10-13')['metrics']
        assert '2026-10-13' in report['USO']['missing_dates']
        assert '2026-10-13' not in report['BNO']['missing_dates']
        restore_working_history(db,user.team_id,'oil_etf',tmp_path)
        restored=json.loads((tmp_path/'oil-history.json').read_text())['2026-10-13']
        assert restored['USO']['futures_barrels']=='16325000'
        assert 'barrels' not in restored['USO']


@pytest.mark.parametrize('corruption',['html_hash','quantity','missing_html'])
def test_archive_must_match_original_bytes_before_any_write(db_factory,seed,corruption):
    with db_factory() as db:
        archive(db,seed['editor'])
        raw=db.scalar(select(BriefSourceSnapshot).where(BriefSourceSnapshot.relative_path.endswith('BNO.html')))
        if corruption=='html_hash':raw.content+=b'changed'
        elif corruption=='missing_html':db.delete(raw)
        else:
            raw.content=raw.content.replace(b'6962',b'9999')
            raw.sha256=hashlib.sha256(raw.content).hexdigest()
        db.flush()
        with pytest.raises(ValueError):recover_oil_futures(db,seed['editor'].team_id)
        assert list(db.scalars(select(BriefDataPoint)))==[]
