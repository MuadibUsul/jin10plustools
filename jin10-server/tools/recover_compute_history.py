"""Actually recover supplied workbook + captured visible history in a new local DB.

No production connection or network is used. Input originals remain read-only.
"""
import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import sys
import uuid

SERVER=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVER))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--gpu-dir',type=Path,required=True)
    parser.add_argument('--token-html',type=Path,required=True)
    parser.add_argument('--as-of',type=date.fromisoformat,required=True)
    args=parser.parse_args()
    out=args.out.resolve()
    if out.exists():parser.error('Choose a new output directory')
    if out.is_relative_to(SERVER/'app') or out.is_relative_to(SERVER/'tests'):
        parser.error('Choose an output outside source code')
    from lxml import html
    from sqlalchemy import create_engine,select
    from sqlalchemy.orm import Session
    from app.db.models import Base,Team,AIComputeObservation
    from app.briefs.public_compute import _parse,_history_prices,PAGES,ORIGIN
    from app.briefs.public_compute_store import restore_workbook_history,store_visible_history,store_visible
    from app.briefs.data_reliability import extract,save_points,coverage,plain
    out.mkdir(parents=True)
    engine=create_engine('sqlite:///'+str(out/'recovered.sqlite'))
    Base.metadata.create_all(engine)
    report={'scope':'isolated_local_database_from_real_saved_materials','production_changed':False,'categories':{}}
    with Session(engine) as db:
        team=Team(id=uuid.uuid4(),name='Local source recovery verification');db.add(team);db.flush()
        for category in ('gpu_rental','token_cost'):
            folder=out/category;folder.mkdir()
            readings=[];histories={};sources=[]
            for metric,slug in PAGES[category].items():
                path=args.token_html if metric=='token' else args.gpu_dir/(metric+'-fresh.html')
                raw=path.read_bytes();url=f'{ORIGIN}/products/silicon-index/{slug}'
                reading=_parse(raw,metric,url,args.as_of)
                readings.append(reading)
                prices=_history_prices(html.fromstring(raw),metric)
                histories[metric]=[{'date':day.isoformat(),'price':str(value)} for day,value in sorted(prices.items())
                                   if day.isoformat()<=reading['date']]
                sources.append({'file':str(path),'sha256':sha256(raw).hexdigest(),'visible_history_rows':len(prices)})
            if len({row['date'] for row in readings})!=1:raise ValueError('Source card dates disagree')
            target=readings[0]['date']
            capture={'category':category,'data_date':target,'captured_at':args.as_of.isoformat(),
                     'readings':readings,'history':histories}
            restored=restore_workbook_history(db,team.id,before=target)
            history_result=store_visible_history(db,team.id,capture)
            current_result=store_visible(db,team.id,capture)
            kind='gpu' if category=='gpu_rental' else 'token'
            rows=[{'date':row.date.isoformat(),'token_price':row.token_price,'a100':row.a100,
                   'h100':row.h100,'b200':row.b200,'status':row.source_status,
                   'revision':row.revision,'public_display':row.public_display} for row in db.scalars(
                       select(AIComputeObservation).where(AIComputeObservation.team_id==team.id,
                           AIComputeObservation.kind==kind).order_by(AIComputeObservation.date))]
            (folder/'ai-compute-source.json').write_text(json.dumps(plain(rows),ensure_ascii=False,indent=2),encoding='utf-8')
            save_points(db,team.id,category,extract(category,folder))
            check=coverage(db,team.id,category,target)
            report['categories'][category]={'workbook_rows_restored':restored,'historical_store':history_result,
                'current_store':current_result,'sources':sources,'coverage':check}
        db.commit()
    (out/'recovery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for category,item in report['categories'].items():
        print(json.dumps({'category':category,'status':item['coverage']['status'],'metrics':{
            key:{'observed_days':value['observed_days'],'missing_dates':value['missing_dates']}
            for key,value in item['coverage']['metrics'].items()}},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
