"""Bounded subprocesses whose entire process tree belongs to one operation."""
import os
import signal
import subprocess


def kill_tree(process):
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15,check=False)
    else:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass


def run_tree(command,*,timeout):
    options=({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True})
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**options)
    try:
        stdout,stderr=process.communicate(timeout=timeout)
        if process.returncode:raise subprocess.CalledProcessError(process.returncode,command,output=stdout,stderr=stderr)
        return subprocess.CompletedProcess(command,process.returncode,stdout,stderr)
    except BaseException:
        kill_tree(process)
        process.communicate(timeout=15)
        raise
