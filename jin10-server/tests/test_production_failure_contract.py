from datetime import date, datetime, timedelta, timezone
import json

import pytest

from app.briefs.failures import MissingObservations, SourceUnavailable, job_failure
from app.briefs.sources import save


def test_barometer_reports_the_exact_missing_series_and_session(tmp_path):
    from app.briefs.engines.us_barometer.run import render
    snapshot={'target':'2026-11-23',
              'wilshire':[{'date':day,'close':70000} for day in ('2026-11-20','2026-11-23')],
              'gdp':[{'period':'2026-07-01','value':33000}],
              'erp':[{'date':'2026-11-20','erp':.06}]}
    with pytest.raises(MissingObservations) as caught:
        render(snapshot,tmp_path,draw=False)
    assert caught.value.source=='股权风险溢价（ERP）'
    assert caught.value.missing_dates==['2026-11-23']
    assert caught.value.code=='required_data_missing'
    snapshot['erp']=[{'date':'2026-11-23','erp':.058}]
    with pytest.raises(MissingObservations) as caught:
        render(snapshot,tmp_path,draw=False)
    assert caught.value.missing_dates==['2026-11-20']
    assert caught.value.code=='required_data_missing'


def test_independent_sources_are_archived_when_one_provider_fails(tmp_path,monkeypatch):
    from app.briefs.engines.us_barometer import run,fetch_data
    calls=[]
    def unavailable():
        raise SourceUnavailable('来源返回 HTTP 503')
    monkeypatch.setattr(fetch_data,'wilshire',unavailable)
    monkeypatch.setattr(fetch_data,'gdp',lambda:[{'period':'2026-07-01','value':33000}])
    monkeypatch.setattr(fetch_data,'streetstats_erp',lambda:[{'date':'2026-11-23','erp':.058}])
    with pytest.raises(SourceUnavailable,match='wilshire'):
        run.fetch('2026-11-23',tmp_path,on_series=lambda name,rows:calls.append(name))
    assert calls==['gdp','erp']
    assert json.loads((tmp_path/'source-failures.json').read_text())[0]['source']=='wilshire'


def test_expired_crashing_task_stops_after_six_attempts(db_factory,seed):
    from app.briefs.service import enqueue
    from app.briefs.worker import claim
    from app.db.models import BriefJob
    now=datetime(2026,11,24,1,tzinfo=timezone.utc)
    with db_factory() as db:
        job=enqueue(db,seed['editor'].team_id,date(2026,11,24),'volume')
        job.status='running';job.attempts=6;job.lease_until=now-timedelta(seconds=1)
        db.commit();ident=job.id
    assert claim(db_factory,now) is None
    with db_factory() as db:
        job=db.get(BriefJob,ident)
        assert job.status=='failed' and job.active_key is None and job.lease_until is None
        assert '连续中断6次' in job.error


def test_worker_probe_rejects_stale_and_future_heartbeats(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.services import production_health
    now=datetime.now(timezone.utc)
    monkeypatch.setattr(production_health,'get_settings',lambda:SimpleNamespace(storage_dir=tmp_path))
    for seconds,expected in ((0,True),(-151,False),(60,False)):
        save(tmp_path/'.brief-worker-heartbeat.json',{'at':(now+timedelta(seconds=seconds)).isoformat(),'state':'idle'})
        assert production_health.worker_status(now)['ready'] is expected
    save(tmp_path/'.brief-worker-heartbeat.json',{'at':'2026-11-24T08:00:00'})
    assert production_health.worker_status(now)['ready'] is False


def test_failure_details_ignore_a_previous_attempt(tmp_path):
    from types import SimpleNamespace
    job=SimpleNamespace(id='job',error='新的来源错误')
    save(tmp_path/'runs/job/error.json',{'message':'上次错误','code':'awaiting_source','source':'old'})
    assert job_failure(job,tmp_path)['source'] is None


def test_health_exposes_failed_job_with_team_scoped_diagnostics(db_factory,seed,monkeypatch):
    from app.briefs.service import enqueue,root_for
    from app.services.production_health import production_status
    from app.config import get_settings
    now=datetime(2026,11,24,4,tzinfo=timezone.utc)
    save(get_settings().storage_dir/'.brief-worker-heartbeat.json',{'at':now.isoformat(),'state':'idle'})
    with db_factory() as db:
        job=enqueue(db,seed['editor'].team_id,date(2026,11,24),'volume')
        job.status='failed';job.error='成交额缺少必要交易日：2026-11-20'
        db.commit()
        save(root_for(job.team_id)/'runs'/str(job.id)/'error.json',{
            'message':job.error,'code':'required_data_missing','source':'jin10',
            'missing_dates':['2026-11-20']})
        report=production_status(db,job.team_id,now)
        item=next(item for item in report['items'] if item['category']=='volume')
        assert item['failure']['missing_dates']==['2026-11-20']
        other=production_status(db,seed['outsider'].team_id,now)
        assert all(alert.get('job_id')!=str(job.id) for alert in other['alerts'])


def test_unexpected_task_crash_is_visible_and_releases_active_key(db_factory,seed,monkeypatch):
    from app.briefs import worker
    from app.briefs.service import enqueue,root_for
    from app.db.models import BriefJob,BriefSourceSnapshot
    from sqlalchemy import select
    with db_factory() as db:
        job=enqueue(db,seed['editor'].team_id,date(2026,11,24),'volume')
        job.status='running';db.commit();ident=job.id
        save(root_for(job.team_id)/'runs'/str(ident)/'volume-collected.json',[])
    def crash(*args):
        raise RuntimeError('unexpected internal failure')
    monkeypatch.setattr(worker,'execute',crash)
    worker.execute_safely(db_factory,ident)
    with db_factory() as db:
        job=db.get(BriefJob,ident)
        assert job.status=='failed' and job.active_key is None and job.lease_until is None
        assert 'RuntimeError' in job.error
        assert db.scalar(select(BriefSourceSnapshot).where(BriefSourceSnapshot.job_id==ident)) is not None


@pytest.mark.parametrize('status,expected_calls',[(503,3),(429,3),(403,1)])
def test_http_failures_have_bounded_retries_without_leaking_tokens(monkeypatch,status,expected_calls):
    import httpx
    from app.briefs.engines.us_barometer import fetch_data
    client=httpx.Client;calls=[]
    def reply(request):
        calls.append(request)
        return httpx.Response(status,text='private response body',request=request)
    monkeypatch.setattr(fetch_data.httpx,'Client',lambda **kwargs:client(transport=httpx.MockTransport(reply),**kwargs))
    monkeypatch.setattr('time.sleep',lambda _:None)
    with pytest.raises(SourceUnavailable) as caught:
        fetch_data.get('https://example.test/data',{'Authorization':'Bearer private-token'})
    assert len(calls)==expected_calls
    assert 'private' not in str(caught.value)


def test_volume_deadline_stops_before_starting_another_network_request(tmp_path,monkeypatch):
    from app.briefs import sources
    state=tmp_path/'history';state.mkdir();out=tmp_path/'run';out.mkdir()
    monkeypatch.setenv('SOURCE_DEADLINE_MONOTONIC','0')
    monkeypatch.setattr(sources,'urlopen',lambda *a,**k:pytest.fail('network called after deadline'))
    with pytest.raises(SourceUnavailable,match='预算'):
        sources.volume('2026-11-23',out,state)


def test_capture_keeps_numeric_evidence_and_excludes_guest_token(tmp_path,monkeypatch):
    import httpx
    from app.briefs.engines.us_barometer import fetch_data
    client=httpx.Client
    def reply(request):
        return httpx.Response(200,content=b'{"riskHistory":[]}' if request.url.path.endswith('/risk')
                              else b'{"token":"private-token"}',request=request)
    monkeypatch.setattr(fetch_data.httpx,'Client',lambda **kwargs:client(transport=httpx.MockTransport(reply),**kwargs))
    with fetch_data.capture(tmp_path):
        fetch_data.get('https://streetstats.finance/api/token')
        fetch_data.get('https://streetstats.finance/api/valuation/market/risk',{'Authorization':'Bearer private-token'})
    files=list(tmp_path.iterdir())
    assert len(files)==2
    original=next(file for file in files if not file.name.endswith('.metadata.json'))
    assert original.read_bytes()==b'{"riskHistory":[]}'
    assert all('private-token' not in file.read_text() for file in files)


def test_alerts_log_changes_and_recovery_without_repeating(db_factory,seed,monkeypatch,caplog):
    import logging
    from app.services import production_health
    from app.db.models import BriefConfig
    with db_factory() as db:
        db.add(BriefConfig(team_id=seed['editor'].team_id,enabled=True));db.commit()
    current=[{'code':'source_unavailable','category':'volume','message':'来源 HTTP 503'}]
    monkeypatch.setattr(production_health,'production_status',lambda *args:{'alerts':list(current)})
    caplog.set_level(logging.INFO,logger='plus.briefs.health')
    production_health.record_alerts(db_factory)
    assert 'HTTP 503' in caplog.text
    caplog.clear();production_health.record_alerts(db_factory)
    assert not caplog.records
    current.clear();production_health.record_alerts(db_factory)
    assert 'recovered' in caplog.text


def test_compute_collection_failure_is_visible_without_enabling_automatic_drafts(db_factory,seed):
    from app.briefs.service import enqueue
    from app.services.production_health import production_status
    from app.db.models import BriefConfig
    with db_factory() as db:
        team=seed['editor'].team_id
        db.add(BriefConfig(team_id=team,enabled=True,data_enabled=True))
        job=enqueue(db,team,date(2026,11,24),'token_cost',user_id=seed['editor'].id)
        job.status='failed';job.error='目标日尚未发布；本次已补回来源能提供的真实历史';db.commit()
        report=production_status(db,team,datetime(2026,11,24,4,tzinfo=timezone.utc))
        item=next(item for item in report['items'] if item['category']=='token_cost')
        assert item['schedule_enabled'] is False and item['acquisition_mode']=='on_generate'
        assert item['job_status']=='failed' and item['failure']
        assert any(alert.get('job_id')==str(job.id) for alert in report['alerts'])
        assert not any(alert['code']=='missing_daily_product' and alert.get('category')=='token_cost' for alert in report['alerts'])


def test_volume_parse_failure_is_not_reported_as_waiting_for_publication(tmp_path,monkeypatch):
    from app.briefs import sources
    from app.briefs.failures import SourceContractError
    from contextlib import contextmanager
    class Response:
        def read(self,*args):return b'{"unexpected":"schema"}'
    @contextmanager
    def fetch(*args,**kwargs):yield Response()
    monkeypatch.setattr(sources,'urlopen',fetch)
    state=tmp_path/'state';state.mkdir();out=tmp_path/'out';out.mkdir()
    with pytest.raises(SourceContractError) as caught:
        sources.volume('2026-11-23',out,state)
    assert caught.value.code=='source_contract_error'


@pytest.mark.parametrize('action,target,should_recover',[
    ('produce',date(2026,11,23),True),
    ('render',date(2026,11,23),False),
    ('produce',date(2026,11,19),False)])
def test_new_verified_capture_recovers_old_collection_alarm(db_factory,seed,action,target,should_recover):
    from app.briefs.service import enqueue
    from app.services.production_health import production_status
    from app.db.models import BriefConfig,BriefDataCheck
    with db_factory() as db:
        team=seed['editor'].team_id
        db.add(BriefConfig(team_id=team,enabled=True,data_enabled=True))
        failed=enqueue(db,team,date(2026,11,24),'volume',action='reconcile')
        failed.status='failed';failed.active_key=None;failed.error='必要交易日缺失'
        failed.updated_at=datetime(2026,11,24,1,tzinfo=timezone.utc);db.flush()
        newer=enqueue(db,team,date(2026,11,24),'volume',action=action,user_id=seed['editor'].id)
        newer.status='succeeded';newer.active_key=None;db.flush()
        db.add(BriefDataCheck(team_id=team,category='volume',data_date=target,
            job_id=newer.id,attempt=1,status='complete',report={'current_data_valid':True},
            created_at=datetime(2026,11,24,2,tzinfo=timezone.utc)))
        db.commit()
        report=production_status(db,team,datetime(2026,11,24,4,tzinfo=timezone.utc))
        item=next(item for item in report['items'] if item['category']=='volume')
        assert item['collection_status']=='failed' and item['collection_failure']
        assert bool(item['collection_recovered_at']) is should_recover
        assert any(a['code'].startswith('collection_') and a.get('category')=='volume'
                   for a in report['alerts']) is not should_recover
