"""Delete unreferenced derived files only after their database transaction commits."""
import logging
import shutil
from pathlib import Path
from sqlalchemy import event

log = logging.getLogger(__name__)


def after_commit_directory(db, path, boundary):
    path, boundary = Path(path).resolve(), Path(boundary).resolve()
    if path == boundary or not path.is_relative_to(boundary):
        raise ValueError('清理路径超出派生文件目录')
    pending = db.info.setdefault('derived_directory_cleanup', set())
    pending.add((path, boundary))
    if db.info.get('derived_cleanup_registered'):
        return
    db.info['derived_cleanup_registered'] = True

    def committed(session):
        # A SAVEPOINT commit is not the outer transaction's durability boundary.
        if session.in_nested_transaction():
            return
        for target, root in session.info.pop('derived_directory_cleanup', set()):
            if target.is_symlink() or target.resolve() != target or not target.is_relative_to(root):
                log.error('Rejected changed derived cleanup path: %s', target)
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
            except OSError:
                log.exception('Deferred derived cleanup failed: %s', target)

    def rolled_back(session):
        session.info.pop('derived_directory_cleanup', None)

    event.listen(db, 'after_commit', committed)
    event.listen(db, 'after_rollback', rolled_back)
