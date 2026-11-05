"""Host-volume fencing plus a continuously monitored PostgreSQL session lock."""
import os
import threading
from sqlalchemy import text

LOCK_ID=71010501


class WorkerLock:
    def __init__(self,engine,path,on_lost,interval=5):
        self.engine=engine;self.path=path;self.on_lost=on_lost;self.interval=interval
        self.stop=threading.Event();self.ready=threading.Event();self.error=None;self.backend_pid=None

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise SystemExit('Another brief worker owns this storage volume')
        if self.engine.dialect.name=='postgresql':
            self.thread=threading.Thread(target=self._monitor,daemon=True,name='brief-lock-monitor');self.thread.start()
            if not self.ready.wait(15):
                self.__exit__(None,None,None);raise SystemExit('Worker lock connection timed out')
            if self.error:
                self.__exit__(None,None,None);raise SystemExit('Another brief worker is active or lock database unavailable')
        return self

    def _monitor(self):
        acquired=False
        try:
            with self.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                if not connection.execute(text('SELECT pg_try_advisory_lock(:key)'),{'key':LOCK_ID}).scalar():
                    raise RuntimeError('worker_lock_busy')
                self.backend_pid=connection.execute(text('SELECT pg_backend_pid()')).scalar()
                acquired=True;self.ready.set()
                try:
                    while not self.stop.wait(self.interval):
                        # A reconnect cannot silently become the lock owner.
                        pid=connection.execute(text('SELECT pg_backend_pid()')).scalar()
                        held=connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND objid=:key AND granted)"),{'key':LOCK_ID}).scalar()
                        if pid!=self.backend_pid or not held:raise RuntimeError('worker_lock_lost')
                finally:
                    if not connection.invalidated:
                        connection.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':LOCK_ID})
        except BaseException as exc:
            self.error=exc;self.ready.set()
            if acquired and not self.stop.is_set():self.on_lost()

    def check(self):
        if self.error:raise SystemExit('Worker exclusive lock was lost')

    def __exit__(self,*args):
        self.stop.set()
        if hasattr(self,'thread'):self.thread.join(timeout=10)
        self.file.close()
