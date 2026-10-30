"""Fill real source gaps from durable evidence and bounded source range reads."""
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from sqlalchemy import select,or_
from app.db.models import BriefDataPoint, BriefSourceSnapshot
from .data_reliability import extract,save_points

FILES={'volume':('plot-data.json',),'turnover':('source.json',),
       'valuation':('erp-plot-data.json','buffett-plot-data.json','pe-published-history.json','all-a-history.json'),
       'liquidity':('liquidity-snapshot.json','partial-history.json'),'us_barometer':('us-snapshot.json',),
       'us_structure':('us-structure-snapshot.json',),
       'token_cost':('ai-compute-source.json','public-compute-read.json'),
       'gpu_rental':('ai-compute-source.json','public-compute-read.json')}
FILES['oil_etf']=('result.json',)


def recover_snapshots(db,team_id,category,folder,*,limit=32,replay_revisions=False):
    """Restore actual hashed captures, never synthesize a missing observation."""
    names=FILES.get(category,());found=set();restored=0
    rows=db.scalars(select(BriefSourceSnapshot).where(BriefSourceSnapshot.team_id==team_id,
        BriefSourceSnapshot.category==category,
        or_(*(BriefSourceSnapshot.relative_path.endswith('/'+name) for name in names),
            BriefSourceSnapshot.relative_path.in_(names))).order_by(
                BriefSourceSnapshot.captured_at.asc() if replay_revisions else BriefSourceSnapshot.captured_at.desc()).limit(limit))
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    for snapshot in rows:
        name=Path(snapshot.relative_path).name
        if name not in names:continue
        # Multiple captures are intentionally ingested: a newer truncated history
        # cannot erase the older prefix. Revisions are processed oldest first by
        # the explicit CLI; startup recovery never overwrites a known point.
        if hashlib.sha256(snapshot.content).hexdigest()!=snapshot.sha256:
            raise ValueError('历史来源快照哈希不符，不能用于补齐')
        path=folder/name;path.write_bytes(snapshot.content)
        payload=json.loads(snapshot.content) if category=='oil_etf' else None
        points=extract(category,folder,payload,only_file=name)
        current={(row.series,row.method,row.data_date.isoformat()) for row in db.scalars(
            select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,BriefDataPoint.category==category))}
        restored+=save_points(db,team_id,category,[point for point in points if replay_revisions or
            (point['series'],point['method'],point['date']) not in current],snapshot.job_id,
            source_snapshot_id=snapshot.id,collected_at=snapshot.captured_at)
        found.add(name)
    return restored


def restore_working_history(db,team_id,category,state):
    """Rebuild lost/truncated working histories from append-only database points."""
    from .sources import read,save
    specs={'volume':{'volume':('volume-history.json','jin10_hushen_turnover_yi_v1')},
        'turnover':{'turnover':('turnover-history.json','legu_count_ratios_ma9_v1')},
        'us_structure':{name:(name+'-history.json','yahoo_daily_close_low_v1') for name in ('spx','ndx','dji','rut','spxew')},
        'us_barometer':{'wilshire':('wilshire-history.json','yahoo_w5000_v1'),
                        'erp':('erp-history.json','streetstats_erp_v1'),
                        'gdp':('gdp-history.json','fred_current_revised_not_point_in_time')}}
    state=Path(state);state.mkdir(parents=True,exist_ok=True);count=0
    rows=list(db.scalars(select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,
        BriefDataPoint.category==category).order_by(BriefDataPoint.collected_at.desc(),BriefDataPoint.revision.desc())))
    for series,(filename,method) in specs.get(category,{}).items():
        chosen={}
        for row in rows:
            if row.series==series and row.method==method:chosen.setdefault(row.data_date.isoformat(),row.values)
        path=state/filename;before=read(path) if path.is_file() else {'method':method,'rows':[]}
        # The GDP working file intentionally identifies the current revised series.
        file_method='fred_current_revised' if series=='gdp' else method
        if before.get('method') not in (method,file_method):raise ValueError('恢复历史时来源方法不一致')
        key='period' if series=='gdp' else 'date'
        existing={row[key]:row for row in before['rows']}
        new={**existing,**chosen};count+=len(new)-len(existing)
        save(path,{'method':file_method,'rows':[new[day] for day in sorted(new)]})
    if category=='liquidity':
        points={}
        for row in rows:
            if row.series in ('DR001','DR007'):
                point=points.setdefault(row.data_date.isoformat(),{'date':row.data_date.isoformat()})
                point.setdefault(row.series,row.values['weighted_rate'])
        path=state/'dr-history.json';existing=read(path)['rows'] if path.exists() else []
        for row in existing:points.setdefault(row['date'],{}).update(row)
        if points:save(path,{'method':'cfets_dr_close_v1','rows':[points[day] for day in sorted(points)]})
    if category=='oil_etf':
        path=state/'oil-history.json';history=read(path) if path.exists() else {}
        for row in rows:
            history.setdefault(row.data_date.isoformat(),{}).setdefault(row.series,row.values)
        if history:save(path,history)
    if category=='valuation':
        raw_path=state/'all-a-history.json';raw_values={}
        for row in rows:
            if row.series=='all_a_market_cap':raw_values.setdefault(row.data_date.isoformat(),dict(row.values))
        if raw_path.exists():raw_values={**{row['date']:row for row in read(raw_path)},**raw_values}
        if raw_values:save(raw_path,[raw_values[day] for day in sorted(raw_values)])
        path=state/'pe-published-history.json';known={}
        for row in rows:
            if row.series=='csi300_pe':known.setdefault(row.data_date.isoformat(),dict(row.values))
        if path.exists():known.update({row['date']:row for row in read(path)})
        restored_files={}
        for day in sorted(known,reverse=True):
            row=known[day];sha=row.get('source_sha256')
            if not sha or Path(row.get('source_file','')).is_file():continue
            if sha not in restored_files and len(restored_files)<32:
                snapshot=db.scalar(select(BriefSourceSnapshot).where(BriefSourceSnapshot.team_id==team_id,
                    BriefSourceSnapshot.category==category,BriefSourceSnapshot.sha256==sha).limit(1))
                if snapshot and hashlib.sha256(snapshot.content).hexdigest()==sha:
                    evidence=state/'pe-source-evidence'/(sha+'.json');evidence.parent.mkdir(exist_ok=True)
                    evidence.write_bytes(snapshot.content);restored_files[sha]=str(evidence.resolve())
            if sha in restored_files:row['source_file']=restored_files[sha]
        if known:save(path,[known[day] for day in sorted(known)])
    return count


def missing_ranges(wanted,existing,*,padding=3):
    """Group adjacent missing sessions into real fetch intervals with overlap."""
    wanted=sorted(set(wanted));missing=set(wanted)-set(existing);groups=[];current=[]
    for day in wanted:
        if day in missing:current.append(day)
        elif current:groups.append(current);current=[]
    if current:groups.append(current)
    return [(date.fromisoformat(group[0])-timedelta(days=padding),
             date.fromisoformat(group[-1])+timedelta(days=padding)) for group in groups]


def repair_ranges(rows,wanted,fetch_range,*,max_requests=12,checkpoint=None):
    merged={row['date']:row for row in rows};requests=0
    for start,end in missing_ranges(wanted,merged)[:max_requests]:
        fetched=fetch_range(start,end);requests+=1
        incoming={}
        for row in fetched:
            stamp=date.fromisoformat(row['date']).isoformat()
            if stamp in incoming:raise ValueError('补抓返回重复日期')
            if not start.isoformat()<=stamp<=end.isoformat():raise ValueError('补抓返回区间外日期')
            incoming[stamp]=row
        # Overlap is evidence of comparability, not permission to scale prices.
        for stamp in set(incoming)&set(merged):
            for key in ('close','low'):
                if key in incoming[stamp] and key in merged[stamp]:
                    from decimal import Decimal
                    if abs(Decimal(str(incoming[stamp][key]))-Decimal(str(merged[stamp][key])))>Decimal('0.01'):
                        raise ValueError('补抓重叠日期数值不一致，不能直接拼接')
        merged.update(incoming)
        if checkpoint:checkpoint([merged[stamp] for stamp in sorted(merged)])
    return [merged[day] for day in sorted(merged)],{'requests':requests,'remaining_dates':sorted(set(wanted)-set(merged))}


def main():
    """Recover existing durable captures into exportable tables; dry run by default."""
    import argparse
    from datetime import datetime
    import uuid
    from app.db.models import Team
    from app.db.session import get_session_factory
    from .policy import CATEGORIES,CHINA,expected_date
    from .data_reliability import coverage
    from .service import root_for
    parser=argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('--team-id',type=uuid.UUID,required=True)
    parser.add_argument('--category',choices=['all',*CATEGORIES],default='all')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--limit',type=int,default=10000)
    parser.add_argument('--issue-date',type=date.fromisoformat,default=datetime.now(CHINA).date())
    args=parser.parse_args()
    if args.out.exists():parser.error('Choose a new report directory')
    if not 1<=args.limit<=100000:parser.error('limit must be 1..100000')
    args.out.mkdir(parents=True)
    report={'applied':args.apply,'team_id':str(args.team_id),'categories':{}}
    factory=get_session_factory()
    with factory() as db:
        if db.get(Team,args.team_id) is None:parser.error('Team does not exist')
        for category in CATEGORIES if args.category=='all' else [args.category]:
            try:
                count=recover_snapshots(db,args.team_id,category,args.out/category,
                    limit=args.limit,replay_revisions=True)
                state=(root_for(args.team_id)/'history'/category if args.apply else args.out/'working'/category)
                restore_working_history(db,args.team_id,category,state)
                status=coverage(db,args.team_id,category,expected_date(category,args.issue_date))
                report['categories'][category]={'restored_revisions':count,**status}
                if args.apply:db.commit()
                else:db.rollback()
            except (ValueError,OSError) as exc:
                db.rollback();report['categories'][category]={'status':'blocked','error':str(exc)}
    (args.out/'recovery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'applied':args.apply,'report':str(args.out/'recovery-report.json')},ensure_ascii=False))


if __name__=='__main__':main()
