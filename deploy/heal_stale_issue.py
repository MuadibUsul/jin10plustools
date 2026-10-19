"""One-off maintenance: rebuild the latest issue of a category with current rules.

Runs inside the application container (it uses the same database and storage):
    PYTHONIOENCODING=utf-8 python /tmp/heal_stale_issue.py --category us_barometer
It appends a new version; it never edits or deletes stored versions.
"""
import argparse
import uuid

from sqlalchemy import select

from app.briefs.service import issue_out
from app.briefs.worker import execute
from app.db.models import BriefIssue, BriefJob, BriefVersion, Role, User
from app.db.session import get_session_factory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--category', default='us_barometer')
    parser.add_argument('--issue-date')
    args = parser.parse_args()
    factory = get_session_factory()
    with factory() as db:
        query = select(BriefIssue).where(BriefIssue.category == args.category)
        if args.issue_date:
            query = query.where(BriefIssue.issue_date == args.issue_date)
        issue = db.scalars(query.order_by(BriefIssue.issue_date.desc()).limit(1)).first()
        if issue is None:
            raise SystemExit(f'没有 {args.category} 的稿件')
        user = (db.scalars(select(User).where(User.team_id == issue.team_id, User.role == Role.ADMIN).limit(1)).first()
                or db.scalars(select(User).where(User.team_id == issue.team_id).limit(1)).first())
        previous = db.scalars(select(BriefVersion).where(BriefVersion.issue_id == issue.id)
                              .order_by(BriefVersion.number.desc()).limit(1)).first()
        print('issue', issue.id, issue.issue_date, 'version', issue.version)
        if previous:
            print('before images', previous.payload.get('images'),
                  'pipeline', previous.payload.get('pipeline'), '图:', '（图）' in previous.dynamic_body)
        job = BriefJob(team_id=issue.team_id, issue_date=issue.issue_date, category=issue.category,
                       action='produce', preview=False, dedupe_key='heal-' + uuid.uuid4().hex,
                       status='running', attempts=1, created_by=user.id if user else None)
        db.add(job)
        db.commit()
        job_id, issue_id = job.id, issue.id
    execute(factory, job_id)
    with factory() as db:
        issue = db.get(BriefIssue, issue_id)
        latest = issue_out(db, issue)['versions'][0]
        stored = db.scalars(select(BriefVersion).where(BriefVersion.issue_id == issue_id)
                            .order_by(BriefVersion.number.desc()).limit(1)).first()
        print('after version', latest['number'], 'images', [item['name'] for item in latest['images']],
              'pipeline', stored.payload.get('pipeline'), '图:', '（图）' in latest['dynamic_body'])
        print('body', latest['dynamic_body'][:160].replace('\n', ' / '))


if __name__ == '__main__':
    main()
