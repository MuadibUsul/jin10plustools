"""One job process: network, plotting and optional API never run in a web request."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from .sources import ADAPTERS, read, save


def generate(category,day,root,run_id,action='produce'):
    root=Path(root);state=root/'history'/category;state.mkdir(parents=True,exist_ok=True)
    out=root/'runs'/run_id;out.mkdir(parents=True,exist_ok=True)
    cache=root/'cache'/category/f'{day}.json'
    previous=read(cache) if cache.exists() else None
    if previous and category=='valuation':
        previous['dynamic_body']=previous['dynamic_body'].replace('全A总市值/上年度全年GDP','A股总市值/上年度全年GDP')
        if previous.get('deepseek',{}).get('body'):
            previous['deepseek']['body']=previous['deepseek']['body'].replace('全A总市值/上年度全年GDP','A股总市值/上年度全年GDP')
    if previous and previous['data_date']!=day:raise ValueError('缓存日期不符')
    if action in ('render','ai') and not previous:raise ValueError('尚无已校验快照，请先生成稿件')
    if previous and action!='refresh':
        source=(root/previous['artifact_dir']).resolve()
        if not source.is_relative_to(root.resolve()):raise ValueError('缓存路径不合法')
        if action!='render':
            for name,digest in previous['image_sha256'].items():
                if hashlib.sha256((source/name).read_bytes()).hexdigest()!=digest:raise ValueError('缓存图片损坏，请从快照重建')
        for name in ('plot-data.json','liquidity-snapshot.json','source.json','oil-source.json','buffett-plot-data.json','erp-plot-data.json'):
            if (source/name).is_file():shutil.copy2(source/name,out/name)
        if action in ('produce','ai'):
            payload=dict(previous)
            if action=='produce':return payload
            for name in payload['images']:shutil.copy2(source/name,out/name)
        else:
            # All render operations read an already captured source, never fetch.
            if category=='volume':
                from .sources import render_volume
                payload=render_volume(read(source/'plot-data.json'),out)
                shutil.copy2(source/'plot-data.json',out/'plot-data.json')
            elif category=='liquidity':
                from .sources import render_liquidity
                payload=render_liquidity(read(source/'liquidity-snapshot.json'),out)
                shutil.copy2(source/'liquidity-snapshot.json',out/'liquidity-snapshot.json')
            elif category=='turnover':
                end=date.fromisoformat(day)
                try:start=end.replace(year=end.year-10)
                except ValueError:start=end.replace(year=end.year-10,day=28)
                command=[sys.executable,str(Path(__file__).parent/'engines/turnover/build_turnover.py'),
                         '--data',str(out/'source.json'),'--out-dir',str(out),'--start',start.isoformat(),'--as-of',day]
                done=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=90)
                if done.returncode:raise ValueError('快照重绘失败，未请求外部数据')
                payload=dict(previous);payload['images']=[next(out.glob('换手率*.png')).name]
            elif category=='valuation':
                from .charts import valuation_chart
                valuation_chart(read(out/'buffett-plot-data.json'),read(out/'erp-plot-data.json'),out)
                payload=dict(previous);payload['images']=['股债性价比.png'];payload['unified_valuation']=True
            else:
                payload=dict(previous)
                for name in payload['images']:shutil.copy2(source/name,out/name)
    else:
        payload=ADAPTERS[category](day,out,state,refresh=action=='refresh')
    if payload['data_date']!=day:raise ValueError('来源数据日与目标日期不符')
    if not payload['title'].strip() or not payload['dynamic_body'].strip():raise ValueError('标题或正文缺失')
    if category!='oil_etf' and not payload['images']:raise ValueError('栏目缺少配图')
    payload['artifact_dir']=out.relative_to(root).as_posix()
    payload['image_sha256']={name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in payload['images']}
    payload['valid']=True
    if action=='render':
        payload['deepseek']=previous.get('deepseek',{'status':'not_configured'})
        save(out/'result.json',payload);save(cache,payload)
        return payload
    payload['deepseek']={'status':'pending'}
    # A complete rule product is durable before the optional model request.
    save(out/'result.json',payload);save(cache,payload)
    from .engines.liquidity.copy_tracks import generate_deepseek
    rule={'title':payload['title'],'body':payload['dynamic_body'],
          'approved_titles':[payload['title']], 'approved_bodies':[payload['dynamic_body']]}
    from .policy import CATEGORIES
    prefix=payload['title'].split('】',1)[0]+'】'
    rule['approved_titles'].append(f'{prefix}{day}{CATEGORIES[category]}数据更新')
    parsed=date.fromisoformat(day)
    alternative=payload['dynamic_body'].replace(day,f'{parsed.year}年{parsed.month}月{parsed.day}日',1)
    if alternative!=payload['dynamic_body']:rule['approved_bodies'].append(alternative)
    payload['deepseek']=generate_deepseek(rule)
    save(out/'result.json',payload);save(cache,payload)
    return payload


def main():
    p=argparse.ArgumentParser();p.add_argument('--category',required=True);p.add_argument('--date',required=True)
    p.add_argument('--root',required=True);p.add_argument('--run-id',required=True)
    p.add_argument('--action',choices=['produce','refresh','render','ai'],default='produce')
    args=p.parse_args()
    try:
        result=generate(args.category,args.date,args.root,args.run_id,args.action)
        save(Path(args.root)/'runs'/args.run_id/'result.json',result)
    except Exception as exc:
        # Never include request headers, model keys or raw response bodies.
        save(Path(args.root)/'runs'/args.run_id/'error.json',{'type':type(exc).__name__,'message':str(exc)[:500]})
        raise SystemExit(3)


if __name__=='__main__':main()
