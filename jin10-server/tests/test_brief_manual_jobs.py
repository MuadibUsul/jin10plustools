from datetime import datetime, timedelta
import json

import pytest

from app.briefs.policy import CHINA
from app.briefs.service import enqueue, root_for
from app.briefs.worker import claim, execute, schedule
from app.db.models import BriefConfig, BriefJob, utcnow
from tests.conftest import login


def freeze(monkeypatch, now):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr('app.api.briefs.datetime', FrozenDatetime)
    monkeypatch.setattr('app.briefs.worker.datetime', FrozenDatetime)


@pytest.mark.parametrize('now', [datetime(2026,10,16,7,tzinfo=CHINA),
                                 datetime(2026,10,16,17,tzinfo=CHINA),
                                 datetime(2026,10,17,17,tzinfo=CHINA)])
def test_manual_today_jobs_outside_schedule(client, db_factory, seed, monkeypatch, now):
    freeze(monkeypatch, now)
    headers=login(client,'editor@plus.test')
    response=client.post('/api/briefs/jobs',headers=headers,
                         json={'category':'volume','issue_date':now.date().isoformat()})
    assert response.status_code==202, response.text
    assert str(claim(db_factory,now))==response.json()['id']
    with db_factory() as db:
        db.add(BriefConfig(team_id=seed['editor'].team_id,enabled=True));db.commit()
    assert schedule(db_factory,now)==0


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('succeeded', [False, True])
@pytest.mark.parametrize('hour', [9, 17])
def test_only_automatic_jobs_obey_cutoff_and_retry(db_factory, seed, monkeypatch, automatic, succeeded, hour):
    now=datetime(2026,10,16,hour,30,tzinfo=CHINA)
    freeze(monkeypatch,now)
    with db_factory() as db:
        job=enqueue(db,seed['editor'].team_id,now.date(),'volume',
                    user_id=None if automatic else seed['editor'].id)
        job.status='running';job.attempts=1;db.commit();job_id=job.id
    folder=root_for(seed['editor'].team_id)/'runs'/str(job_id)
    folder.mkdir(parents=True)
    if succeeded:
        (folder/'result.json').write_text(json.dumps({'valid':True,'title':'Verified',
            'dynamic_body':'Verified facts.','data_date':'2026-10-15',
            'artifact_dir':'runs/'+str(job_id),'images':[]}),encoding='utf-8')
    class Process:
        pid=123
        def __init__(self,*args,**kwargs): self.calls=0
        def poll(self):
            self.calls+=1
            return None if self.calls==1 else 0
        def kill(self): pass
        def wait(self): return 0
    monkeypatch.setattr('app.briefs.worker.subprocess.Popen',Process)
    monkeypatch.setattr('app.briefs.worker.time.sleep',lambda _:None)
    monkeypatch.setattr('app.briefs.worker.os.killpg',lambda *args:None,raising=False)
    execute(db_factory,job_id)
    with db_factory() as db:
        job=db.get(BriefJob,job_id)
        expected=('skipped' if automatic and hour==17 else 'succeeded') if succeeded else (
            'waiting' if automatic and hour==9 else 'failed')
        assert job.status==expected
        if not automatic: assert job.active_key is None


def test_manual_click_runs_waiting_automatic_retry_immediately(db_factory, seed):
    with db_factory() as db:
        job=enqueue(db,seed['editor'].team_id,datetime(2026,10,16).date(),'volume')
        job.status='waiting';job.next_attempt_at=utcnow()+timedelta(hours=2)
        db.commit();job_id=job.id
        manual=enqueue(db,job.team_id,job.issue_date,job.category,user_id=seed['editor'].id,unique=True)
        db.commit()
        assert manual.id==job_id
        assert manual.status=='queued' and manual.created_by==seed['editor'].id
        assert manual.next_attempt_at<=utcnow()


def test_manual_history_future_and_removed_preview_mode(client, monkeypatch):
    freeze(monkeypatch,datetime(2026,10,17,17,tzinfo=CHINA))
    headers=login(client,'editor@plus.test')
    body={'category':'volume','issue_date':'2026-10-15'}
    assert client.post('/api/briefs/jobs',headers=headers,json=body).status_code==202
    assert client.post('/api/briefs/jobs',headers=headers,json={**body,'preview':True}).status_code==422
    assert client.post('/api/briefs/jobs',headers=headers,json={**body,'issue_date':'2026-10-18'}).status_code==422
