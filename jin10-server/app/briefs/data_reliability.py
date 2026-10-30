"""Durable, team-scoped source observations and explicit historical coverage."""
from datetime import date, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math

from sqlalchemy import select,func
from sqlalchemy.orm import aliased
from app.db.models import BriefDataPoint, BriefDataCheck,BriefSourceSnapshot,utcnow
from .trading_calendar import CalendarUnavailable, market_for, sessions

HORIZON={'volume':366,'turnover':3653,'valuation':3653,'liquidity':90,
         'oil_etf':30,'us_barometer':3653,'us_structure':3653,'token_cost':92,'gpu_rental':92}
EXPECTED={'volume':('volume',),'turnover':('turnover',),'valuation':('erp','buffett'),
          'liquidity':('DR001','DR007'),'oil_etf':('USO','BNO'),
          'us_barometer':('wilshire','erp','gdp'),'us_structure':('spx','ndx','dji','rut','spxew'),
          'token_cost':('token',),'gpu_rental':('a100','h100','b200')}
UNITS={'volume':{'turn':'CNY_100_million','vol':'source_native'},
       'turnover':{'totalCompany':'company_count','belowPercent3':'company_count','belowPercent5':'company_count','close':'index_points'},
       'valuation':{'close':'index_points','pe_ttm_mcw':'times','pe_ttm_mcw_raw':'times','bond_yield_number':'percent_number',
           'erp':'fraction','erp_percent':'percent_number','marketCap':'CNY_100_million','market_cap_yi':'CNY_100_million','gdp':'CNY_100_million'},
       'liquidity':{'weighted_rate':'percent_number'},
       'oil_etf':{'barrels':'barrels','futures_barrels':'barrels','change_barrels':'barrels','reference_price':'USD_per_barrel'},
       'us_structure':{'close':'index_points','low':'index_points'},
       'us_barometer':{'close':'index_points','erp':'fraction','value':'USD_billion_SAAR'},
       'gpu_rental':{'price':'USD_per_GPU_hour'},'token_cost':{'price':'USD_per_million_tokens'}}


def plain(value):
    if isinstance(value,dict):return {str(key):plain(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):return [plain(item) for item in value]
    if isinstance(value,Decimal):
        if not value.is_finite():raise ValueError('数据含非有限数字')
        return str(value)
    if isinstance(value,float):
        if not math.isfinite(value):raise ValueError('数据含非有限数字')
        return str(value)
    return value


def digest(value):
    def semantic(item):
        if isinstance(item,dict):return {key:semantic(val) for key,val in item.items()
            if key not in {'source_file','source_sha256','captured_at','raw_evidence','source_job_id'}}
        if isinstance(item,list):return [semantic(val) for val in item]
        return item
    return hashlib.sha256(json.dumps(semantic(plain(value)),sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def extract(category,folder,payload=None,*,only_file=None):
    """Read captured normalized facts, never infer observations from prose."""
    result=[]
    def load(name):
        if only_file is not None and name!=only_file:return None
        path=folder/name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else None
    def add(series,rows,method,key='date'):
        for row in rows or []:
            stamp=date.fromisoformat(row[key]).isoformat()
            result.append({'series':series,'date':stamp,'method':method,'values':plain(row)})
    if category=='volume':add('volume',load('plot-data.json'),'jin10_hushen_turnover_yi_v1')
    elif category=='turnover':
        raw=load('source.json') or {}
        add('turnover',raw.get('rows',raw.get('data',[])) if isinstance(raw,dict) else raw,'legu_count_ratios_ma9_v1')
    elif category=='valuation':
        add('csi300_pe',load('pe-published-history.json'),'snowball_fund_danjuan_public')
        add('all_a_market_cap',load('all-a-history.json'),'eastmoney_all_a_total_market_cap_v1')
        for row in load('erp-plot-data.json') or []:
            method=(str(row['pe_source'])+'|'+str(row['bond_source'])) if row.get('pe_source') and row.get('bond_source') else 'legacy_erp_method_unverified'
            add('erp',[row],method)
        for row in load('buffett-plot-data.json') or []:
            add('buffett',[row],row.get('source') or 'legacy_buffett_method_unverified')
    elif category=='liquidity':
        raw=load('liquidity-snapshot.json') or {}
        if not raw:raw={'history':load('partial-history.json') or {}}
        for series in EXPECTED[category]:
            add(series,[{'date':row['date'],'weighted_rate':row[series]} for row in
                         raw.get('history',{}).get('rows',[]) if row.get(series) is not None], 'cfets_dr_close_v1')
            for row in raw.get('quotes',{}).get('rows',[]):
                if row['product']==series:
                    add(series,[{'date':raw['quotes']['quote_date'],'weighted_rate':row['weighted_rate']}], 'cfets_dr_close_v1')
    elif category=='oil_etf' and payload:
        for fund in payload.get('facts',{}).get('funds',[]):
            add(fund['ticker'],[{'date':payload['data_date'],**fund}],'issuer_futures_and_swap_estimate_v1')
    elif category=='us_barometer':
        raw=load('us-snapshot.json') or {}
        add('wilshire',raw.get('wilshire',[]),'yahoo_w5000_v1')
        add('erp',raw.get('erp',[]),'streetstats_erp_v1')
        add('gdp',raw.get('gdp',[]),'fred_current_revised_not_point_in_time',key='period')
    elif category=='us_structure':
        raw=load('us-structure-snapshot.json') or {}
        for series,rows in raw.get('data',{}).items():add(series,rows,'yahoo_daily_close_low_v1')
    elif category in ('token_cost','gpu_rental'):
        rows=load('ai-compute-source.json') or []
        for row in rows:
            for series in EXPECTED[category]:
                field='token_price' if series=='token' else series
                if row.get(field) is not None:
                    method=('editor_manual' if row.get('status')=='manual_entry' else
                            'silicondata_token_usd_per_million_v1' if category=='token_cost' else
                            'silicondata_gpu_usd_per_hour_v1')
                    add(series,[{'date':row['date'],'price':row[field],
                                 'source_status':row.get('status')}],method)
        if not rows:
            captured=load('public-compute-read.json') or {}
            method='silicondata_token_usd_per_million_v1' if category=='token_cost' else 'silicondata_gpu_usd_per_hour_v1'
            for series,history in captured.get('history',{}).items():add(series,history,method)
            for reading in captured.get('readings',[]):
                add(reading['metric'],[{'date':reading['date'],'price':reading['price']}],method)
    return result


def save_points(db,team_id,category,points,job_id=None,*,source_snapshot_id=None,collected_at=None):
    points=[{**item,'values':plain(item['values'])} for item in points]
    if not points:return 0
    start=min(item['date'] for item in points);end=max(item['date'] for item in points)
    history={};max_revision={};effective=collected_at or utcnow()
    def stamp(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    query=select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,BriefDataPoint.category==category,
        BriefDataPoint.data_date>=date.fromisoformat(start),BriefDataPoint.data_date<=date.fromisoformat(end)).order_by(
        BriefDataPoint.collected_at.desc(),BriefDataPoint.revision.desc())
    for row in db.scalars(query):
        key=(row.series,row.method,row.data_date.isoformat())
        history.setdefault(key,[]).append(row)
        max_revision[key]=max(max_revision.get(key,0),row.revision)
    count=0
    for item in points:
        key=(item['series'],item['method'],item['date'])
        old=next((row for row in history.get(key,[]) if stamp(row.collected_at)<=stamp(effective)),None)
        units={key:unit for key,unit in UNITS[category].items() if key in item['values']}
        sha=digest({'values':item['values'],'units':units})
        if old and old.sha256==sha:continue
        new=BriefDataPoint(team_id=team_id,category=category,series=item['series'],method=item['method'],
            data_date=date.fromisoformat(item['date']),revision=max_revision.get(key,0)+1,
            values=item['values'],units=units,sha256=sha,job_id=job_id,source_snapshot_id=source_snapshot_id)
        new.collected_at=effective
        db.add(new);history.setdefault(key,[]).append(new)
        history[key].sort(key=lambda row:(stamp(row.collected_at),row.revision),reverse=True)
        max_revision[key]=new.revision;count+=1
    db.flush()
    return count


def link_sources(db,job):
    """Link normalized rows to original durable artifacts, independently of drafts."""
    snapshots=list(db.scalars(select(BriefSourceSnapshot).where(BriefSourceSnapshot.job_id==job.id,
                                                             BriefSourceSnapshot.attempt==job.attempts)))
    names={'volume':'plot-data.json','turnover':'source.json','liquidity':'liquidity-snapshot.json',
        'oil_etf':'result.json','us_barometer':'us-snapshot.json','us_structure':'us-structure-snapshot.json',
        'token_cost':'ai-compute-source.json','gpu_rental':'ai-compute-source.json'}
    for point in db.scalars(select(BriefDataPoint).where(BriefDataPoint.job_id==job.id,
                                                       BriefDataPoint.source_snapshot_id.is_(None))):
        name=names.get(job.category)
        if job.category=='valuation':name={'erp':'erp-plot-data.json','buffett':'buffett-plot-data.json',
                                           'csi300_pe':'pe-published-history.json','all_a_market_cap':'all-a-history.json'}[point.series]
        source=next((row for row in snapshots if row.relative_path==name),None)
        if source is None and job.category in ('token_cost','gpu_rental'):
            source=next((row for row in snapshots if row.relative_path=='public-compute-read.json'),None)
        if source:point.source_snapshot_id=source.id;point.collected_at=source.captured_at


def read_series(db,team_id,category,series,start,end,*,as_of=None,limit=5000):
    """Latest per date and method from evidence actually collected by as_of."""
    filters=[BriefDataPoint.team_id==team_id,BriefDataPoint.category==category,
        BriefDataPoint.series==series,BriefDataPoint.data_date>=start,BriefDataPoint.data_date<=end]
    if as_of is not None:filters.append(BriefDataPoint.collected_at<=as_of)
    ranked=select(BriefDataPoint,func.row_number().over(
        partition_by=(BriefDataPoint.data_date,BriefDataPoint.method),
        order_by=(BriefDataPoint.collected_at.desc(),BriefDataPoint.revision.desc())).label('rank')).where(*filters).subquery()
    point=aliased(BriefDataPoint,ranked)
    rows=list(db.scalars(select(point).where(ranked.c.rank==1).order_by(point.data_date,point.method).limit(limit+1)))
    return {'items':[{'data_date':row.data_date.isoformat(),'method':row.method,'revision':row.revision,
        'values':row.values,'units':row.units,'collected_at':row.collected_at.isoformat(),
        'source_snapshot_id':str(row.source_snapshot_id) if row.source_snapshot_id else None,
        'job_id':str(row.job_id) if row.job_id else None} for row in rows[:limit]],'truncated':len(rows)>limit}


def coverage(db,team_id,category,target):
    target=date.fromisoformat(target) if isinstance(target,str) else target
    start=target-timedelta(days=HORIZON[category]); market=market_for(category)
    metrics={};limitations=[]
    try:wanted=set(sessions(market,start,target))
    except CalendarUnavailable as exc:
        wanted=None;limitations.append(str(exc))
    rows=list(db.scalars(select(BriefDataPoint).where(BriefDataPoint.team_id==team_id,
        BriefDataPoint.category==category,BriefDataPoint.data_date>=start,BriefDataPoint.data_date<=target)))
    for series in EXPECTED[category]:
        chosen=[row for row in rows if row.series==series]
        days={row.data_date.isoformat() for row in chosen};methods=sorted({row.method for row in chosen})
        missing=sorted(wanted-days) if wanted is not None and series!='gdp' else []
        metrics[series]={'requested_start':start.isoformat(),'requested_end':target.isoformat(),
            'first_date':min(days) if days else None,'last_date':max(days) if days else None,
            'observed_days':len(days),'missing_dates':missing,'methods':methods,
            'complete':bool(days) and not missing and wanted is not None,
            'comparable_history':len(methods)==1 and not any('unverified' in method for method in methods)}
        if series=='gdp':
            metrics[series]['frequency']='quarterly'
            metrics[series]['complete']=None
            limitations.append('GDP为当前修订序列，不能证明历史时点已公布的原始版本')
        if len(methods)>1:limitations.append(f'{series}存在多种来源/方法，未认定历史同口径')
    return {'target_date':target.isoformat(),'market':market,'metrics':metrics,
            'limitations':sorted(set(limitations)),
            'status':'complete' if metrics and all(item['complete'] is True and item['comparable_history']
                 for item in metrics.values()) and not limitations else 'partial_history'}


def record_run(db,job,folder,payload,error=None):
    from .policy import expected_date
    target=date.fromisoformat(payload['data_date']) if payload else expected_date(job.category,job.issue_date)
    points=extract(job.category,folder,payload)
    save_points(db,job.team_id,job.category,points,job.id)
    report=coverage(db,job.team_id,job.category,target)
    # Compare source business values, not images or wording. Corrections found
    # by a later repair do not silently leave a stale draft confirmable.
    current={(item['series'],item['method'],item['date']):item for item in points if item['date']<=target.isoformat()}
    if current:report['publication_digest']=digest([current[key] for key in sorted(current)])
    report['current_data_valid']=bool(payload and payload.get('valid'))
    if not report['current_data_valid']:
        report['status']='failed';report['error']=str(error or '本次未取得完整且通过校验的数据')[:500]
    if payload:
        payload['data_reliability']=report
        if report['status']=='partial_history':
            missing=sum(len(item['missing_dates']) for item in report['metrics'].values())
            payload.setdefault('warnings',[]).append(f'历史覆盖尚未完整核验（缺失记录合计{missing}项）；详见数据状态')
    existing=db.scalar(select(BriefDataCheck).where(BriefDataCheck.job_id==job.id,BriefDataCheck.attempt==job.attempts))
    if existing:existing.status=report['status'];existing.report=report
    else:db.add(BriefDataCheck(team_id=job.team_id,category=job.category,data_date=target,
        job_id=job.id,attempt=job.attempts,status=report['status'],report=report))
    db.flush()
    return report
