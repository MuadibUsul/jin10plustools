"""Run the user-authorized AI repair against existing captured data, then verify retention."""
import json
import time
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import select,func
from app.db.models import BriefIssue,BriefVersion,BriefJob,User,Role
from app.db.session import get_session_factory
from app.briefs.service import enqueue,prune_versions,root_for

factory=get_session_factory()
team=uuid.UUID('e3d2d207-ea80-40ef-b8c6-8121893f55a5')
with factory() as db:
    user=db.scalar(select(User).where(User.team_id==team,User.is_active.is_(True),User.role.in_([Role.ADMIN,Role.EDITOR])))
    assert user
    jobs=[]
    for issue in db.scalars(select(BriefIssue).where(BriefIssue.team_id==team,BriefIssue.issue_date==date(2026,10,16))):
        if not issue.version:continue
        job=enqueue(db,team,issue.issue_date,issue.category,user_id=user.id,unique=True)
        jobs.append(job.id)
    db.commit()
    print('Queued snapshot reuse plus AI repair for',len(jobs),'current editions',flush=True)
deadline=time.monotonic()+900
reported={}
while True:
    with factory() as db:
        pending=False
        for id in jobs:
            job=db.get(BriefJob,id)
            if job.status in ('queued','running','waiting'):pending=True
            if reported.get(id)!=job.status:
                print(job.category,job.status,flush=True);reported[id]=job.status
    if not pending:break
    assert time.monotonic()<deadline,'Timed out waiting for AI repair'
    time.sleep(4)
with factory() as db:
    for id in jobs:
        job=db.get(BriefJob,id)
        data=json.loads((root_for(team)/'runs'/str(id)/'result.json').read_text())
        ai=data.get('deepseek',{})
        print(json.dumps({'category':job.category,'job_status':job.status,'ai_status':ai.get('status'),'reason':ai.get('reason'),'finish_reason':ai.get('finish_reason'),'model':ai.get('model'),'usage':ai.get('usage')},ensure_ascii=False),flush=True)
        assert job.status=='succeeded' and ai.get('status')=='ok',job.category
    for issue in db.scalars(select(BriefIssue).where(BriefIssue.team_id==team).with_for_update()):
        prune_versions(db,issue)
    db.commit()
    for issue in db.scalars(select(BriefIssue).where(BriefIssue.team_id==team)):
        count=db.scalar(select(func.count()).select_from(BriefVersion).where(BriefVersion.issue_id==issue.id))
        assert count<=2
    print('All current AI results succeeded; all issues retain at most two deliverable revisions',flush=True)
