"""Losslessly deduplicate legacy original bytes; dry-run unless --apply is given."""
import argparse,hashlib,json,uuid
from sqlalchemy import select
from app.db.session import get_session_factory
from app.db.models import BriefSourceSnapshot,Team
from .source_archive import evidence_blob


def compact(db,team_id,*,limit=100):
    rows=list(db.scalars(select(BriefSourceSnapshot).where(BriefSourceSnapshot.team_id==team_id,
        BriefSourceSnapshot.blob_id.is_(None)).order_by(BriefSourceSnapshot.id).limit(limit)))
    logical=0
    for row in rows:
        original=row.content
        if len(original)!=row.byte_count or hashlib.sha256(original).hexdigest()!=row.sha256:raise ValueError('旧证据校验失败，停止整理')
        blob=evidence_blob(db,team_id,row.sha256,original)
        if blob.content!=original:raise ValueError('证据无损还原失败')
        row.blob=blob;row._content=b'';logical+=len(original)
    db.flush()
    return {'files':len(rows),'logical_bytes_preserved':logical}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--team-id',type=uuid.UUID,required=True)
    p.add_argument('--batch',type=int,default=100);p.add_argument('--apply',action='store_true');a=p.parse_args()
    if not 1<=a.batch<=500:p.error('batch must be 1..500')
    total=0;logical=0
    while True:
        with get_session_factory()() as db:
            if db.get(Team,a.team_id) is None:p.error('Team not found')
            result=compact(db,a.team_id,limit=a.batch)
            if a.apply:db.commit()
            else:db.rollback()
        total+=result['files'];logical+=result['logical_bytes_preserved']
        if not a.apply or result['files']==0:break
    print(json.dumps({'applied':a.apply,'files':total,'original_bytes_preserved':logical}))

if __name__=='__main__':main()
