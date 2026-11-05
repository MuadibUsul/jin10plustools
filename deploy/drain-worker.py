"""Own a bounded drain lease; stop only an idle PLUS worker under a DB lock."""
import argparse,signal,subprocess,time,uuid

MARK_CODE=r'''
import fcntl,json,sys,time
from app.config import get_settings
root=get_settings().storage_dir
root.mkdir(parents=True,exist_ok=True)
path=root/'.brief-worker-drain'
owner,mode=sys.argv[1:]
with (root/'.brief-drain-state.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 try:saved=json.loads(path.read_text()) if path.exists() else {}
 except (ValueError,OSError):saved={'owner':'legacy','expires_at':path.stat().st_mtime+1800}
 if mode=='create':
  if saved.get('owner')!=owner and saved.get('expires_at',0)>time.time():raise SystemExit('Another drain owns the lease')
  temp=path.with_suffix('.tmp')
  temp.write_text(json.dumps({'owner':owner,'expires_at':time.time()+1800}))
  temp.replace(path)
 elif saved.get('owner')==owner:path.unlink(missing_ok=True)
'''

LOCK_CODE=r'''
import sys
from sqlalchemy import text
from app.db.session import get_session_factory
with get_session_factory()() as db:
 db.execute(text("SET LOCAL lock_timeout = '3s'"))
 db.execute(text('LOCK TABLE brief_jobs IN SHARE MODE'))
 busy=db.execute(text("SELECT count(*) FROM brief_jobs WHERE status='running'")).scalar()
 if busy:print('BUSY',flush=True)
 else:
  print('IDLE_LOCKED',flush=True)
  sys.stdin.read(1)
'''

def marker(owner,mode):
    subprocess.run(['docker','exec','plus-api','python','-c',MARK_CODE,owner,mode],check=True,timeout=20)


def main():
    p=argparse.ArgumentParser();p.add_argument('--worker',required=True);p.add_argument('--timeout',type=int,default=600)
    p.add_argument('--owner',default=uuid.uuid4().hex);p.add_argument('--clear',action='store_true')
    args=p.parse_args()
    labels=subprocess.check_output(['docker','inspect','-f','{{index .Config.Labels "com.docker.compose.project"}}:{{index .Config.Labels "com.docker.compose.service"}}',args.worker],text=True).strip()
    if labels!='plus:brief-worker':raise SystemExit('Unexpected worker ownership')
    if args.clear:marker(args.owner,'clear');return
    stopped=False;created=False
    signal.signal(signal.SIGTERM,lambda *args:(_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        marker(args.owner,'create');created=True;deadline=time.monotonic()+args.timeout
        while time.monotonic()<deadline:
            child=subprocess.Popen(['docker','exec','-i','plus-api','python','-c',LOCK_CODE],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                line=child.stdout.readline().strip()
                if line=='IDLE_LOCKED':
                    subprocess.run(['docker','stop','--time','20',args.worker],check=True,stdout=subprocess.DEVNULL,timeout=30)
                    stopped=True
                    child.communicate('x',timeout=10)
                    print('PLUS worker drained and stopped without interrupting a job',flush=True);return
                child.communicate(timeout=10)
            finally:
                if child.poll() is None:child.kill();child.communicate()
            print('Waiting for the active PLUS task to finish',flush=True);time.sleep(5)
        raise SystemExit('Worker drain timed out before replacing services')
    finally:
        if created and not stopped:marker(args.owner,'clear')

if __name__=='__main__':main()
