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
from .progress import record


def explain_result(out, category, payload):
    from .process_details import describe
    details = describe(category, payload)
    for source in details['sources']:
        record(out,4,'结果来源：'+source['label'])
    for calculation in details['calculations']:
        record(out,4,'计算核对：'+calculation)


def generate(category,day,root,run_id,action='produce'):
    root=Path(root);state=root/'history'/category;state.mkdir(parents=True,exist_ok=True)
    out=root/'runs'/run_id;out.mkdir(parents=True,exist_ok=True)
    record(out,1,'正在检查同日期的数据与图片快照')
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
        for name in ('plot-data.json','liquidity-snapshot.json','source.json','oil-source.json','oil-previous-source.json','buffett-plot-data.json','erp-plot-data.json','us-snapshot.json','us-structure-snapshot.json'):
            if (source/name).is_file():shutil.copy2(source/name,out/name)
        if action in ('produce','ai'):
            payload=dict(previous)
            import os
            configured=bool(os.getenv('DEEPSEEK_API_KEY') and os.getenv('DEEPSEEK_MODEL'))
            ai=previous.get('deepseek',{})
            if action=='produce' and (not configured or (ai.get('status')=='ok' and ai.get('model')==os.getenv('DEEPSEEK_MODEL'))):
                record(out,4,'同日期快照校验通过，复用已保存的稿件与图片')
                explain_result(out,category,payload)
                return payload
            record(out,4,'复用已校验数据和图片，补做AI文案处理')
            for name in payload['images']:shutil.copy2(source/name,out/name)
        else:
            record(out,3,'正在使用原始快照重绘图片，不请求外部数据')
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
                payload['turnover_watermark_removed']=True
            elif category=='valuation':
                from .charts import valuation_chart
                valuation_chart(read(out/'buffett-plot-data.json'),read(out/'erp-plot-data.json'),out)
                payload=dict(previous);payload['images']=['股债性价比.png'];payload['unified_valuation']=True
            elif category=='us_barometer':
                from .engines.us_barometer.run import render
                payload=render(read(out/'us-snapshot.json'),out)
            elif category=='us_structure':
                from .engines.us_structure.run import render
                payload=render(read(out/'us-structure-snapshot.json'),out)
            else:
                payload=dict(previous)
                for name in payload['images']:shutil.copy2(source/name,out/name)
    else:
        record(out,2,f'正在获取并校验 {day} 的来源数据')
        payload=ADAPTERS[category](day,out,state,refresh=action=='refresh')
    record(out,4,'来源日期、标题、正文及图片已完成基础校验')
    explain_result(out,category,payload)
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
    import os
    record(out,4,'正在请求AI文案表达' if os.getenv('DEEPSEEK_API_KEY') and os.getenv('DEEPSEEK_MODEL') else '未启用AI表达，使用已完成的规则稿')
    rule={'title':payload['title'],'body':payload['dynamic_body'],
          'approved_titles':[payload['title']], 'approved_bodies':[payload['dynamic_body']]}
    from .policy import CATEGORIES
    prefix=payload['title'].split('】',1)[0]+'】'
    rule['approved_titles'].append(f'{prefix}{day}{CATEGORIES[category]}数据更新')
    parsed=date.fromisoformat(day)
    alternative=payload['dynamic_body'].replace(day,f'{parsed.year}年{parsed.month}月{parsed.day}日',1)
    if alternative!=payload['dynamic_body']:rule['approved_bodies'].append(alternative)
    payload['deepseek']=generate_deepseek(rule)
    if payload['deepseek'].get('status')=='ok':record(out,4,'AI表达已通过校验，规则稿同时保留')
    elif payload['deepseek'].get('status') not in ('not_configured','disabled'):
        record(out,4,'AI表达未完成，完整规则稿已保留')
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
        from .progress import read
        folder=Path(args.root)/'runs'/args.run_id
        record(folder,read(folder).get('step',1),'执行失败：'+str(exc)[:500])
        raise SystemExit(3)


if __name__=='__main__':main()
