"""A product's source dependencies, separate from the full archival inventory."""
from datetime import date,timedelta
import json,hashlib,tempfile
from pathlib import Path
from .data_reliability import extract,digest

VERSION=3

FILES={'volume':('plot-data.json',),'turnover':('source.json',),
    'valuation':('buffett-plot-data.json','erp-plot-data.json'),
    'liquidity':('liquidity-snapshot.json',),'oil_etf':('result.json',),
    'us_barometer':('us-snapshot.json',),'us_structure':('us-structure-snapshot.json',),
    'token_cost':('ai-compute-source.json',),'gpu_rental':('ai-compute-source.json',)}


def publication_inputs(category,folder,payload,*,version=VERSION):
    target=payload['data_date'];points=[]
    for name in FILES[category]:points.extend(extract(category,folder,payload,only_file=name))
    points=[p for p in points if p['date']<=target]
    if category in ('turnover','valuation','us_barometer','us_structure'):
        end=date.fromisoformat(target)
        try:start=end.replace(year=end.year-10)
        except ValueError:start=end.replace(year=end.year-10,day=28)
        if category=='turnover':start-=timedelta(days=45) # MA9 context at the first plotted session.
        start=min(start.isoformat(),((payload.get('facts') or {}).get('editorial_features') or {}).get('comparison_start',start.isoformat()))
        older_gdp=[p for p in points if p['series']=='gdp' and p['date']<start]
        prior=max((p['date'] for p in older_gdp),default=None)
        points=[p for p in points if p['date']>=start or p['series']=='gdp' and p['date']==prior]
    keyed={(p['series'],p['method'],p['date']):p for p in points}
    extra={}
    def read(name):
        path=folder/name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    if category=='liquidity' and version>=3:
        raw=read('liquidity-snapshot.json');quotes=raw.get('quotes') or {}
        extra['quotes']={'quote_date':quotes.get('quote_date'),'rows':quotes.get('rows',[])}
        extra['history']=[r for r in (raw.get('history') or {}).get('rows',[]) if r['date']<=target]
        bank=raw.get('central_bank')
        extra['central_bank']=({k:bank.get(k) for k in ('date','term_days','rate_percent','bid_yi','awarded_yi','operations','full_demand_met')}
                               if bank else None)
    elif category in ('token_cost','gpu_rental') and version>=3:
        metrics={'token'} if category=='token_cost' else {'a100','h100','b200'}
        capture=read('public-compute-read.json');extra['display']=[]
        for row in read('ai-compute-source.json') or []:
            if row['date']!=target:continue
            public=row.get('public_display') or (capture if row.get('status')=='public_visible_summary' else {})
            displays={'public_display':public,'manual_display':row.get('manual_display') or {}}
            item={'date':row['date'],**{kind:[v for v in value.get('readings',[])
                if v.get('metric') in metrics and v.get('date')==target] for kind,value in displays.items()}}
            required='manual_display' if row.get('status')=='manual_entry' else 'public_display' if row.get('status')=='public_visible_summary' else None
            if required and not metrics<={v['metric'] for v in item[required]}:
                raise ValueError('来源缺少当期公开或人工变化快照，请重新补查')
            extra['display'].append(item)
    return {'version':VERSION,'files':list(FILES[category]),'points':[keyed[k] for k in sorted(keyed)],'extra':extra}


def publication_digest(category,folder,payload,*,version=VERSION):
    values=publication_inputs(category,folder,payload,version=version)
    inputs={'version':version,'category':category,'data_date':payload['data_date'],'points':values['points']}
    if version>=3:inputs['extra']=values['extra']
    return digest(inputs) if values['points'] else None


def archived_check_digest(db,check):
    """Upgrade a legacy gate from immutable source bytes, never from current DB prices."""
    from sqlalchemy import select
    from app.db.models import BriefSourceSnapshot
    if check.job_id is None:return None
    names=set(FILES[check.category])|{'public-compute-read.json'}
    paths=names|{'origin/'+name for name in names}
    snapshots={r.relative_path:r for r in db.scalars(select(BriefSourceSnapshot).where(
        BriefSourceSnapshot.team_id==check.team_id,BriefSourceSnapshot.job_id==check.job_id,
        BriefSourceSnapshot.attempt==check.attempt,BriefSourceSnapshot.category==check.category,
        BriefSourceSnapshot.relative_path.in_(paths)))}
    with tempfile.TemporaryDirectory(prefix='plus-source-verify-') as temp:
        folder=Path(temp)
        for name in names:
            row=snapshots.get(name) or snapshots.get('origin/'+name)
            if row is None:
                if name in FILES[check.category]:return None
                continue
            content=row.content
            if hashlib.sha256(content).hexdigest()!=row.sha256:raise ValueError('来源存档校验失败')
            (folder/name).write_bytes(content)
        return publication_digest(check.category,folder,{'data_date':check.data_date.isoformat()})
