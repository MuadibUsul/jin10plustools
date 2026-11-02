import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.briefs.engines.valuation.fetch_free_pe import fetch_pe, normalize_pe, verified_capture, FetchPEError
from app.briefs.source_archive import import_pe_capture, restore_pe_capture
from app.db.models import BriefJob, BriefSourceSnapshot


def response(values):
    return json.dumps({'result_code': 0, 'data': {'index_eva_pe_growths': [
        {'ts': int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000), 'pe': value}
        for day, value in values.items()]}}).encode()


def capture(folder):
    folder.mkdir(parents=True, exist_ok=True)
    raw = response({'2026-10-13': '13.1697', '2026-10-14': '13.0883'})
    raw_path = folder/'pe-original-history.json'
    raw_path.write_bytes(raw)
    data = normalize_pe(raw, None, '2026-10-14', '2026-10-13')
    data['fetched_at'] = '2026-10-15T17:20:22+08:00'
    # An old absolute path is portable; actual bytes are read from beside the manifest.
    data['raw_evidence'] = [{'file': '/retired/location/'+raw_path.name,
                            'sha256': hashlib.sha256(raw).hexdigest()}]
    normalized = folder/'pe-original-normalized.json'
    normalized.write_text(json.dumps(data), encoding='utf-8')
    return normalized


def test_live_current_session_can_use_verified_previous_capture(tmp_path):
    capture(tmp_path)
    live = response({'2026-10-13': '13.1697', '2026-10-15': '13.2'})
    with patch('app.briefs.engines.valuation.fetch_free_pe._download', return_value=live) as request:
        result = fetch_pe('2026-10-15', '2026-10-14', tmp_path)
        assert request.call_count == 1
        assert result['rows'][0]['pe_ttm_mcw_raw'] == '13.0883'
        assert result['rows'][1]['pe_ttm_mcw_raw'] == '13.2'
        assert result['previous_session_recovered']['date'] == '2026-10-14'
        assert len(result['raw_evidence']) == 2
        assert verified_capture(next(p for p in tmp_path.glob('pe-*-normalized.json') if p.name != 'pe-original-normalized.json'))
        # A completed, verified pair remains replayable even if the live page changes.
        assert fetch_pe('2026-10-15', '2026-10-14', tmp_path)['network_requests_this_call'] == 0
        assert request.call_count == 1


@pytest.mark.parametrize('failure', ['missing_current', 'force_refresh', 'corrupt_raw', 'forged_normalized_value'])
def test_recovery_never_invents_or_uses_unverified_observations(tmp_path, failure):
    manifest = capture(tmp_path)
    if failure == 'corrupt_raw':
        (tmp_path/'pe-original-history.json').write_bytes(b'corrupt')
    if failure == 'forged_normalized_value':
        data = json.loads(manifest.read_bytes()); data['rows'][-1]['pe_ttm_mcw_raw'] = '99'
        manifest.write_text(json.dumps(data), encoding='utf-8')
    live = response({'2026-10-13': '13.1697', **({} if failure == 'missing_current' else {'2026-10-15': '13.2'})})
    with patch('app.briefs.engines.valuation.fetch_free_pe._download', return_value=live):
        with pytest.raises(FetchPEError, match='lacks required sessions'):
            fetch_pe('2026-10-15', '2026-10-14', tmp_path, use_completed_cache=failure != 'force_refresh')


def test_import_only_pe_evidence_and_restore_for_following_session(db_factory, seed, tmp_path):
    manifest = capture(tmp_path/'retired')
    with db_factory() as db:
        job = BriefJob(team_id=seed['editor'].team_id, issue_date=date(2026, 10, 15),
                       category='valuation', dedupe_key='failed-pe', attempts=1, status='failed', error='original error')
        db.add(job); db.flush()
        assert import_pe_capture(db, job, manifest) == 2
        assert import_pe_capture(db, job, manifest) == 2
        assert db.query(BriefSourceSnapshot).count() == 2
        assert job.status == 'failed' and job.error == 'original error'
        next_job = BriefJob(team_id=job.team_id, issue_date=date(2026, 10, 16),
                            category='valuation', dedupe_key='following-session', attempts=1)
        db.add(next_job); db.flush()
        target = tmp_path/'new-run'
        assert restore_pe_capture(db, next_job, target, '2026-10-15')
        raw_output = target/'erp/raw/pe'
        assert verified_capture(raw_output/manifest.name)
        with patch('app.briefs.engines.valuation.fetch_free_pe._download',
                   return_value=response({'2026-10-15': '13.2'})):
            assert fetch_pe('2026-10-15', '2026-10-14', raw_output)['previous_session_recovered']
        outsider = BriefJob(team_id=seed['outsider'].team_id, issue_date=date(2026, 10, 16),
                           category='valuation', dedupe_key='other-team')
        db.add(outsider); db.flush()
        assert not restore_pe_capture(db, outsider, tmp_path/'outsider', '2026-10-15')


def test_missing_historical_pe_is_not_retried_as_a_delayed_current_release():
    with pytest.raises(FetchPEError) as failure:
        normalize_pe(response({'2026-10-26':'12.9085','2026-10-30':'12.8654'}),None,'2026-10-30','2026-10-29')
    assert failure.value.retryable is False
    assert failure.value.missing_dates==['2026-10-29']
    assert '恢复该日原始记录' in failure.value.user_message
    with pytest.raises(FetchPEError) as pending:
        normalize_pe(response({'2026-10-29':'12.9'}),None,'2026-10-30','2026-10-29')
    assert pending.value.retryable is True


def test_failed_job_raw_reply_restores_exact_previous_day_and_survives_cache_replay(db_factory,seed,tmp_path):
    from app.briefs.source_archive import restore_daily_pe
    from app.briefs.repair import restore_working_history
    from app.db.models import BriefDataPoint
    from sqlalchemy import select
    with db_factory() as db:
        team=seed['editor'].team_id
        old=BriefJob(team_id=team,issue_date=date(2026,10,30),category='valuation',
                     dedupe_key='failed-raw-only',status='failed',attempts=1)
        db.add(old);db.flush()
        raw=response({'2026-10-28':'12.95','2026-10-29':'12.9123'})
        snapshot=BriefSourceSnapshot(team_id=team,job_id=old.id,attempt=1,category='valuation',
            data_date=date(2026,10,30),relative_path='erp/raw/pe/pe-old-history.json',
            content=raw,sha256=hashlib.sha256(raw).hexdigest(),byte_count=len(raw))
        db.add(snapshot);db.flush()
        later_copy=BriefSourceSnapshot(team_id=team,job_id=old.id,attempt=2,category='valuation',
            data_date=date(2026,10,30),relative_path='erp/raw/pe/pe-copy-history.json',
            content=raw,sha256=snapshot.sha256,byte_count=len(raw),
            captured_at=snapshot.captured_at.replace(year=2027))
        db.add(later_copy);db.flush()
        current=BriefJob(team_id=team,issue_date=date(2026,11,2),category='valuation',dedupe_key='recover-oct29',attempts=1)
        db.add(current);db.flush()
        folder=tmp_path/'run'
        assert restore_daily_pe(db,current,folder,'2026-10-30')==1
        point=db.scalar(select(BriefDataPoint))
        assert point.source_snapshot_id==snapshot.id and point.job_id==old.id
        assert point.collected_at.replace(tzinfo=timezone.utc)==snapshot.captured_at.replace(tzinfo=timezone.utc)
        restore_working_history(db,team,'valuation',tmp_path/'working')
        assert json.loads((tmp_path/'working/pe-published-history.json').read_text())[0]['pe_ttm_mcw_raw']=='12.9123'
        with patch('app.briefs.engines.valuation.fetch_free_pe._download',return_value=response({'2026-10-30':'12.8654'})):
            fetched=fetch_pe('2026-10-30','2026-10-29',folder/'erp/raw/pe',use_completed_cache=False)
        assert fetched['rows'][0]['pe_ttm_mcw_raw']=='12.9123'
        manifest=next((folder/'erp/raw/pe').glob('pe-*-normalized.json'))
        assert verified_capture(manifest) is not None
        assert all(Path(item['file']).parent==manifest.parent for item in fetched['raw_evidence'])
        outsider=BriefJob(team_id=seed['outsider'].team_id,issue_date=date(2026,11,2),
                          category='valuation',dedupe_key='cannot-recover-another-team',attempts=1)
        db.add(outsider);db.flush()
        assert restore_daily_pe(db,outsider,tmp_path/'other','2026-10-30')==0


@pytest.mark.parametrize('failed_endpoint',['history','detail'])
def test_daily_pe_capture_preserves_the_other_successful_endpoint(tmp_path,monkeypatch,failed_endpoint):
    from app.briefs.valuation_repair import available_pe
    from app.briefs.engines.valuation import fetch_free_pe as pe
    day='2026-10-30';timestamp=int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()*1000)
    detail=json.dumps({'result_code':0,'data':{'index_code':'SH000300','ts':timestamp,'pe':'12.8654'}}).encode()
    def download(url):
        if (url==pe.HISTORY_URL)==(failed_endpoint=='history'):raise pe.FetchPEError('network_error','fixture unavailable')
        return response({day:'12.8654'}) if url==pe.HISTORY_URL else detail
    monkeypatch.setattr(pe,'_download',download)
    monkeypatch.setattr('app.briefs.trading_calendar.completed_session',lambda _:date(2026,10,30))
    result=available_pe(tmp_path/'pe',history_path=tmp_path/'state/pe-published-history.json')
    assert str(result[day])=='12.8654'
    assert json.loads((tmp_path/'state/pe-published-history.json').read_text())[0]['source_sha256']
