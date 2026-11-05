"""Fault paths use disposable processes and mocks, never production services."""
import importlib.util,json,os,subprocess,sys,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine


def test_storage_lock_fences_a_second_worker(tmp_path):
    from app.briefs.worker_lock import WorkerLock
    engine=create_engine('sqlite://')
    with WorkerLock(engine,tmp_path/'worker.lock',lambda:None):
        with pytest.raises(SystemExit,match='storage volume'):
            with WorkerLock(engine,tmp_path/'worker.lock',lambda:None):pass
    with WorkerLock(engine,tmp_path/'worker.lock',lambda:None):pass


@pytest.mark.skipif(os.name=='nt',reason='POSIX process-group integration runs in Linux CI')
def test_timeout_kills_conversion_grandchild(tmp_path):
    from app.services.processes import run_tree
    pidfile=tmp_path/'child-pid'
    code="import subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):run_tree([sys.executable,'-c',code,str(pidfile)],timeout=1)
    assert pidfile.is_file()
    pid=int(pidfile.read_text())
    for _ in range(20):
        path=Path(f'/proc/{pid}/stat')
        if not path.exists() or path.read_text().split()[2]=='Z':break
        time.sleep(.05)
    else:pytest.fail('Conversion grandchild survived timeout')


def test_drain_timeout_clears_only_its_own_marker(monkeypatch):
    path=Path(__file__).parents[2]/'deploy/drain-worker.py'
    spec=importlib.util.spec_from_file_location('drain_audit',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(sys,'argv',['drain-worker.py','--worker','fixture-worker','--owner','fixture-owner','--timeout','0'])
    monkeypatch.setattr(module.subprocess,'check_output',lambda *a,**k:'plus:brief-worker')
    marker=Mock();monkeypatch.setattr(module,'marker',marker)
    monkeypatch.setattr(module.signal,'signal',lambda *a:None)
    with pytest.raises(SystemExit,match='timed out'):module.main()
    assert marker.call_args_list[0].args==('fixture-owner','create')
    assert marker.call_args_list[-1].args==('fixture-owner','clear')


def test_drain_lease_expires_after_an_uncatchable_interruption(db_factory,monkeypatch):
    from app.config import get_settings
    from app.briefs.worker import drain_requested
    path=get_settings().storage_dir/'.brief-worker-drain';path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'owner':'interrupted','expires_at':time.time()-1}))
    assert drain_requested() is False
    path.write_text(json.dumps({'owner':'active','expires_at':time.time()+300}))
    assert drain_requested() is True


@pytest.mark.skipif(os.name=='nt',reason='Linux deployment lease integration')
def test_drain_cleanup_cannot_clear_another_releases_lease(tmp_path,monkeypatch):
    import app.config
    path=Path(__file__).parents[2]/'deploy/drain-worker.py'
    spec=importlib.util.spec_from_file_location('drain_ownership',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(app.config,'get_settings',lambda:SimpleNamespace(storage_dir=tmp_path))
    monkeypatch.setattr(sys,'argv',['marker','release-a','create']);exec(module.MARK_CODE,{})
    marker=tmp_path/'.brief-worker-drain';assert json.loads(marker.read_text())['owner']=='release-a'
    monkeypatch.setattr(sys,'argv',['marker','release-b','clear']);exec(module.MARK_CODE,{})
    assert marker.exists()
    monkeypatch.setattr(sys,'argv',['marker','release-a','clear']);exec(module.MARK_CODE,{})
    assert not marker.exists()


@pytest.mark.skipif(os.name=='nt',reason='Linux release shell failure injection')
@pytest.mark.parametrize('stage,resumes',[('worker_stopped',True),('api_replacing',False)])
def test_release_failure_cleanup_is_stage_scoped(tmp_path,stage,resumes):
    source=(Path(__file__).parents[2]/'deploy/actions-release.sh').read_text()
    cleanup=source[source.index('finish_release() {'):source.index('python3 "$release_dir/deploy/drain-worker.py" --worker "$worker_id" --owner "$drain_owner"\nrelease_stage=worker_stopped')]
    trace=tmp_path/'trace'
    setup='''set -Eeuo pipefail
docker(){ echo "docker:$*" >> "$TRACE"; if [[ "$1" == inspect ]]; then echo false; fi; }
python3(){ echo "python:$*" >> "$TRACE"; }
revision=fixture-revision
worker_id=fixture-worker
release_image=fixture-image
release_dir=fixture-release-dir
drain_owner=fixture-owner
'''
    env={**os.environ,'TRACE':str(trace),'release_stage':stage}
    result=subprocess.run(['bash','-c',setup+cleanup+'\nexit 7\n'],env=env,capture_output=True,text=True)
    assert result.returncode==7,result.stderr
    calls=trace.read_text()
    assert ('docker:start fixture-worker' in calls) is resumes
    assert '--volumes-from fixture-worker' in calls and '.brief-deployment-failure.json' in calls
    assert 'compose' not in calls and 'image rm' not in calls
