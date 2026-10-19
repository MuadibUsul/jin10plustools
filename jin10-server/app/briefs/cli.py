"""Import captured review samples for UI testing, without live requests or confirmation."""
import argparse
import json
import shutil
import uuid
from datetime import date
from pathlib import Path
from sqlalchemy import select

from app.db.models import Team
from app.db.session import get_session_factory
from .policy import CATEGORIES
from .service import ensure_issue,owned,append_version,root_for


def import_samples(team_name):
    samples=Path(__file__).parent/'samples'
    with get_session_factory()() as db:
        team=db.scalars(select(Team).where(Team.name==team_name)).one_or_none()
        if not team:raise ValueError('团队不存在，请先按现有项目说明创建团队与用户')
        for category in CATEGORIES:
            source=samples/category
            if not (source/'result.json').is_file():continue
            payload=json.loads((source/'result.json').read_text(encoding='utf-8'))
            target=root_for(team.id)/'runs'/f'import-{uuid.uuid4().hex}'
            shutil.copytree(source,target)
            payload['artifact_dir']=target.relative_to(root_for(team.id)).as_posix()
            payload['preview']=True
            payload['warnings']=list(payload.get('warnings',[]))+['已归档来源样本，仅用于网页测试，不是本次实时取数']
            issue=ensure_issue(db,team.id,date(2026,10,15),category);issue=owned(db,team.id,issue.id,True)
            append_version(db,issue,payload,kind='imported')
        db.commit()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['import-samples']);parser.add_argument('--team',required=True)
    args=parser.parse_args();import_samples(args.team);print('样稿已导入2026-10-15，均未确认。')
