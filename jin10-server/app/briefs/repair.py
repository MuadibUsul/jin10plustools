"""Fill real source gaps from durable evidence and bounded source range reads."""
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from sqlalchemy import select,or_
from app.db.models import BriefDataPoint, BriefSourceSnapshot
from .data_reliability import extract,save_points

FILES={'volume':('plot-data.json','volume-collected.json'),'turnover':('source.json','turnover-collected.json'),
       'valuation':('erp-plot-data.json','buffett-plot-data.json','pe-published-history.json','all-a-history.json'),
       'liquidity':('liquidity-snapshot.json','partial-history.json'),'us_barometer':('us-snapshot.json','us-collected.json'),
       'us_structure':('us-structure-snapshot.json','us-structure-collected.json'),
       'token_cost':('ai-compute-source.json','public-compute-read.json'),
       'gpu_rental':('ai-compute-source.json','public-compute-read.json')}
FILES['oil_etf']=('result.json',)


def recover_oil_futures(db,team_id,*,limit=256):
    """Read existing dated holdings archives, including their original HTML.

    USO futures are a separate series: they cannot replace its total exposure,
    which also includes swaps. BNO's published measure is futures holdings.
    """
    from .engines.oil_etf.previous import parse,PAGES
    method='companiesmarketcap_dated_futures_v1'
    known={(row.series,row.data_date.isoformat()) for row in db.scalars(
        select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,
            BriefDataPoint.category=='oil_etf'))}
    manifests=db.scalars(select(BriefSourceSnapshot).where(
        BriefSourceSnapshot.team_id==team_id,BriefSourceSnapshot.category=='oil_etf',
        BriefSourceSnapshot.relative_path.like('%source-archive/%/manifest.json')
        ).order_by(BriefSourceSnapshot.captured_at.asc()).limit(limit))
    restored=0;seen=set()
    for manifest in manifests:
        if manifest.sha256 in seen:continue
        seen.add(manifest.sha256)
        if hashlib.sha256(manifest.content).hexdigest()!=manifest.sha256:
            raise ValueError('持仓归档清单哈希不符')
        data=json.loads(manifest.content);day=date.fromisoformat(data['data_date']).isoformat()
        if set(data['funds'])!=set(PAGES):raise ValueError('持仓归档清单不完整')
        prefix=manifest.relative_path.rsplit('/',1)[0]+'/'
        verified=[]
        for ticker in PAGES:
            raw=db.scalar(select(BriefSourceSnapshot).where(
                BriefSourceSnapshot.team_id==team_id,BriefSourceSnapshot.job_id==manifest.job_id,
                BriefSourceSnapshot.attempt==manifest.attempt,
                BriefSourceSnapshot.relative_path==prefix+ticker+'.html'))
            if raw is None:raise ValueError('持仓归档缺少原始网页')
            if hashlib.sha256(raw.content).hexdigest()!=raw.sha256:
                raise ValueError('持仓归档原始网页哈希不符')
            fact=parse(ticker,raw.content,day)
            if fact!=data['funds'][ticker]:raise ValueError('持仓归档数量与原始网页不符')
            series='USO_futures' if ticker=='USO' else 'BNO'
            values={'date':day,**fact,'source_status':'verified_existing_futures_archive'}
            if ticker=='BNO':values['barrels']=fact['futures_barrels']
            verified.append((series,values,raw))
        for series,values,raw in verified:
            if (series,day) in known:continue
            restored+=save_points(db,team_id,'oil_etf',[
                {'date':day,'series':series,'method':method,'values':values}],manifest.job_id,
                source_snapshot_id=raw.id,collected_at=max(manifest.captured_at,raw.captured_at))
            known.add((series,day))
    return restored


def recover_snapshots(db,team_id,category,folder,*,limit=32,replay_revisions=False):
    """Restore actual hashed captures, never synthesize a missing observation."""
    names=FILES.get(category,());found=set()
    restored=recover_oil_futures(db,team_id) if category=='oil_etf' else 0
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
            if row.method=='issuer_futures_and_swap_estimate_v1':ticker=row.series
            elif row.method=='companiesmarketcap_dated_futures_v1':
                ticker='USO' if row.series=='USO_futures' else row.series
            else:continue
            history.setdefault(row.data_date.isoformat(),{}).setdefault(ticker,row.values)
        if history:save(path,history)
    if category=='valuation':
        for series,filename in (('buffett','buffett-history.json'),('erp','erp-history.json')):
            path=state/filename;history=read(path) if path.exists() else []
            dates={row['date'] for row in history}
            for row in rows:
                if row.series==series and row.data_date.isoformat() not in dates:
                    history.append(dict(row.values));dates.add(row.data_date.isoformat());count+=1
            if history:save(path,sorted(history,key=lambda row:row['date']))
        raw_path=state/'all-a-history.json';raw_values={}
        for row in rows:
            if row.series=='all_a_market_cap':raw_values.setdefault(row.data_date.isoformat(),dict(row.values))
        if raw_path.exists():raw_values={**{row['date']:row for row in read(raw_path)},**raw_values}
        if raw_values:save(raw_path,[raw_values[day] for day in sorted(raw_values)])
        path=state/'pe-published-history.json';known={}
        for row in rows:
            if row.series=='csi300_pe':known.setdefault(row.data_date.isoformat(),dict(row.values))
        if path.exists():known={**{row['date']:row for row in read(path)},**known}
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


def repair_ranges(rows,wanted,fetch_range,*,max_requests=12,checkpoint=None,attempts=None,attempt_checkpoint=None):
    merged={row['date']:row for row in rows};requests=0
    attempts=attempts if attempts is not None else {}
    ranges=missing_ranges(wanted,merged)
    # Never let permanently absent early dates starve every later hole. New
    # ranges go first; otherwise retry the least recently attempted range.
    ranges.sort(key=lambda span:(attempts.get(span[0].isoformat()+'..'+span[1].isoformat(),{}).get('order',0),span[0]))
    order=max((entry.get('order',0) for entry in attempts.values()),default=0)
    errors=[]
    for start,end in ranges[:max_requests]:
        attempt_key=start.isoformat()+'..'+end.isoformat();requests+=1;order+=1
        attempts[attempt_key]={'order':order,'attempts':attempts.get(attempt_key,{}).get('attempts',0)+1}
        if attempt_checkpoint:attempt_checkpoint(attempts)
        try:fetched=fetch_range(start,end)
        except (OSError,ValueError,RuntimeError) as exc:
            attempts[attempt_key]['error']=str(exc)[:300]
            errors.append({'start':start.isoformat(),'end':end.isoformat(),'error':str(exc)[:300]})
            if attempt_checkpoint:attempt_checkpoint(attempts)
            continue
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
        attempts[attempt_key]['returned_dates']=sorted(incoming)
        if attempt_checkpoint:attempt_checkpoint(attempts)
        if checkpoint:checkpoint([merged[stamp] for stamp in sorted(merged)])
    return [merged[day] for day in sorted(merged)],{'requests':requests,'remaining_dates':sorted(set(wanted)-set(merged)),
        'source_errors':errors,'attempted_ranges':len(attempts)}


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
                if category=='valuation':
                    from types import SimpleNamespace
                    from .source_archive import restore_daily_pe
                    count+=restore_daily_pe(db,SimpleNamespace(team_id=args.team_id),args.out/category,
                        expected_date(category,args.issue_date).isoformat(),limit=min(args.limit,10000))
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
