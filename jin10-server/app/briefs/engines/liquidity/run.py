"""Independent daily liquidity job; wait for current-day DR007, then stop."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fetch_data import CHINA,SourceError,fetch_snapshot,read_replay
from copy_tracks import build_copy,generate_deepseek
from delivery import write_delivery,complete_body

HERE=Path(__file__).resolve().parent


def save_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as file:
        if not path.stat().st_size:file.write(b'0');file.flush()
        file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise SourceError('busy','Another liquidity job is running') from None
        yield


def copy_markdown(path,result):
    path.write_text(f"# {result['title']}\n\n{complete_body('liquidity',result['body'])}\n\n数据来源：中国货币网。\n",encoding='utf-8')


def fingerprint(snapshot,logo):
    digest=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode())
    for name in ('run.py','fetch_data.py','render.py','copy_tracks.py','delivery.py'):
        digest.update((HERE/name).read_bytes())
    for path in sorted((HERE/'assets').glob('*')):
        if path.is_file():digest.update(path.read_bytes())
    if logo:digest.update(logo.read_bytes())
    return digest.hexdigest()[:12]


def artifacts_valid(out,manifest):
    try:
        for name,expected in manifest['artifact_sha256'].items():
            if hashlib.sha256((out/name).read_bytes()).hexdigest()!=expected:return False
        return True
    except (OSError,KeyError,TypeError):return False


def make_job(args,state):
    day=args.expected_date or datetime.now(CHINA).date().isoformat()
    replay=args.quotes is not None or args.history is not None
    if replay and (not args.quotes or not args.history or not args.expected_date):
        raise SourceError('configuration_error','Replay needs both --quotes and --history plus --expected-date')
    logo=args.logo or HERE/'assets/plus-original.png'
    if not logo.is_file():raise SourceError('missing_original_logo','Configure the original user-provided Plus logo file')
    if args.logo is None:
        origin=json.loads((HERE/'assets/logo-provenance.json').read_text(encoding='utf-8'))
        if hashlib.sha256(logo.read_bytes()).hexdigest()!=origin['sha256']:
            raise SourceError('logo_changed','Bundled logo differs from the original user-provided asset')
    data_dir=state/'daily-data'/day
    if replay:
        snapshot=read_replay(args.quotes,args.history,day)
    elif (data_dir/'snapshot.json').exists() and not args.refresh:
        snapshot=json.loads((data_dir/'snapshot.json').read_text(encoding='utf-8'))
        # Validate stored source bytes; cached data never acquire a new source time.
        check=read_replay(data_dir/'prr-md.json',data_dir/'prr-chrt.csv',day)
        if check['quotes']!=snapshot['quotes'] or check['history']!=snapshot['history']:
            raise SourceError('cache_corrupt','Saved normalized snapshot differs from its original response')
    else:
        errors=[]
        for attempt in range(args.retries+1):
            attempt_dir=state/'attempts'/f'{day}-{uuid4().hex[:10]}'
            try:
                snapshot=fetch_snapshot(attempt_dir,day)
                # Only fully validated daily data can be reused or published.
                data_dir.mkdir(parents=True,exist_ok=True)
                for name in ('prr-md.json','prr-chrt.csv'):
                    (data_dir/name).write_bytes((attempt_dir/name).read_bytes())
                save_json(data_dir/'snapshot.json',snapshot)
                break
            except SourceError as exc:
                errors.append(exc.code)
                if exc.code!='network_error' or attempt==args.retries:raise
                time.sleep(min(2**attempt,4))
    key=fingerprint(snapshot,logo)
    out=args.out_dir or state/('replays' if replay else 'outputs')/f'{day}-{key}'
    out=out.resolve();out.mkdir(parents=True,exist_ok=True)
    manifest_path=out/'manifest.json'
    if manifest_path.exists() and not args.retry_ai:
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('revision')==key and artifacts_valid(out,manifest):
            return {**manifest,'reused':True}
    from render import render_poster
    png,svg,audit=render_poster(snapshot,out,logo_path=logo)
    rule=build_copy(snapshot)
    copy_markdown(out/'规则版快讯.md',rule)
    save_json(out/'核验记录.json',audit);save_json(out/'数据快照.json',snapshot)
    save_json(out/'规则版快讯.json',rule)
    manifest={'mode':'source-replay' if replay else 'live','data_date':day,'revision':key,
              'output_dir':str(out),'phase':'rules_ready','chart':png.name,'vector':svg.name,
              'source_updated_at':snapshot['quotes']['updated_at'],
              'history_range':[snapshot['history']['start'],snapshot['history']['end']],
              'rules':{'status':'ok','file':'规则版快讯.md'},'deepseek':{'status':'pending'},
              'release_ready':audit['release_ready'],'published':False}
    save_json(manifest_path,manifest)
    write_delivery(out,category='liquidity',data_date=day,rule=rule,images=[png],image_required=True,
                   deepseek={'status':'pending'})
    if not replay:save_json(state/'latest.json',manifest)
    print(json.dumps({'event':'rules_ready','output_dir':str(out)},ensure_ascii=False),flush=True)
    ai={'status':'disabled'} if args.no_ai else generate_deepseek(rule)
    save_json(out/'DeepSeek状态.json',ai)
    manifest['deepseek']={'status':ai['status']}
    if ai['status']=='ok':copy_markdown(out/'DeepSeek版快讯.md',ai);manifest['deepseek']['file']='DeepSeek版快讯.md'
    manifest['phase']='complete'
    write_delivery(out,category='liquidity',data_date=day,rule=rule,images=[png],image_required=True,deepseek=ai)
    tracked=[png.name,svg.name,'核验记录.json','数据快照.json','规则版快讯.md','规则版快讯.json','DeepSeek状态.json']
    if ai['status']=='ok':tracked.append('DeepSeek版快讯.md')
    tracked+=['标题.txt','正文.txt','发布稿.md','交付清单.json']
    if ai['status']=='ok':tracked+=['标题_DeepSeek.txt','正文_DeepSeek.txt']
    manifest['artifact_sha256']={name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in tracked}
    save_json(manifest_path,manifest)
    if not replay:save_json(state/'latest.json',manifest)
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',type=Path,default=HERE/'data')
    parser.add_argument('--out-dir',type=Path)
    parser.add_argument('--expected-date')
    parser.add_argument('--quotes',type=Path)
    parser.add_argument('--history',type=Path)
    parser.add_argument('--logo',type=Path)
    parser.add_argument('--refresh',action='store_true',help='Explicitly fetch another intraday revision')
    parser.add_argument('--retries',type=int,default=2,help='Retries only for transient network failures')
    parser.add_argument('--no-ai',action='store_true')
    parser.add_argument('--retry-ai',action='store_true')
    args=parser.parse_args(argv);state=args.state_dir.resolve();state.mkdir(parents=True,exist_ok=True)
    try:
        if not 0<=args.retries<=3:raise SourceError('configuration_error','Retries must be between zero and three')
        with lock(state/'job.lock'):
            result=make_job(args,state)
            status={'status':'ok','checked_at':datetime.now(CHINA).isoformat(),'result':result}
            save_json(state/'status.json',status)
        print(json.dumps(status,ensure_ascii=False,indent=2));return 0
    except SourceError as exc:
        status={'status':exc.code,'message':str(exc),'checked_at':datetime.now(CHINA).isoformat()}
        if exc.code!='busy':save_json(state/'status.json',status)
        print(json.dumps(status,ensure_ascii=False),file=sys.stderr)
        return 75 if exc.code=='busy' else 3
    except Exception as exc:
        status={'status':'failed','error_type':type(exc).__name__,'checked_at':datetime.now(CHINA).isoformat()}
        save_json(state/'status.json',status);print(json.dumps(status,ensure_ascii=False),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
