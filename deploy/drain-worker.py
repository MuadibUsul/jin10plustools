"""Stop an idle worker while a DB lock prevents a claim race. No secrets leave API."""
import argparse,subprocess,time

LOCK_CODE=r'''
import sys
from sqlalchemy import text
from app.db.session import get_session_factory
from app.config import get_settings
(get_settings().storage_dir/'.brief-worker-drain').touch()
with get_session_factory()() as db:
 db.execute(text("SET LOCAL lock_timeout = '3s'"))
 db.execute(text('LOCK TABLE brief_jobs IN SHARE MODE'))
 busy=db.execute(text("SELECT count(*) FROM brief_jobs WHERE status='running'")).scalar()
 if busy:print('BUSY',flush=True)
 else:
  print('IDLE_LOCKED',flush=True)
  sys.stdin.read(1)
'''

def main():
    p=argparse.ArgumentParser();p.add_argument('--worker',required=True);p.add_argument('--timeout',type=int,default=600)
    args=p.parse_args();deadline=time.monotonic()+args.timeout
    labels=subprocess.check_output(['docker','inspect','-f','{{index .Config.Labels "com.docker.compose.project"}}:{{index .Config.Labels "com.docker.compose.service"}}',args.worker],text=True).strip()
    if labels!='plus:brief-worker':raise SystemExit('Unexpected worker ownership')
    while time.monotonic()<deadline:
        child=subprocess.Popen(['docker','exec','-i','plus-api','python','-c',LOCK_CODE],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=child.stdout.readline().strip()
            if line=='IDLE_LOCKED':
                subprocess.run(['docker','stop','--time','20',args.worker],check=True,stdout=subprocess.DEVNULL,timeout=30)
                child.communicate('x',timeout=10)
                print('PLUS worker drained and stopped without interrupting a job',flush=True);return
            child.communicate(timeout=10)
        finally:
            if child.poll() is None:child.kill();child.communicate()
        print('Waiting for the active PLUS task to finish',flush=True);time.sleep(5)
    raise SystemExit('Worker drain timed out; release stopped before replacing services')

if __name__=='__main__':main()
