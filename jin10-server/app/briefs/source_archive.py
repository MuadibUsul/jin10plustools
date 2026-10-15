"""Persist each brief job's original and normalized data in PostgreSQL.

The filesystem remains the rendering workspace.  Database copies are keyed by
job, attempt and relative path, and retain the exact bytes with a digest.
Images are handled separately by the image-delivery contract.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select

from app.db.models import BriefSourceSnapshot
from .policy import expected_date

DATA_EXTENSIONS = frozenset({'.json', '.csv', '.tsv', '.html', '.xml', '.txt',
                             '.md', '.xls', '.xlsx', '.parquet'})
MAX_FILE_BYTES = 10_000_000
MAX_RUN_BYTES = 100_000_000


def archive_run(db, job, folder: Path, *, history_dir: Path | None = None,
                source_dir: Path | None = None, version=None):
    """Archive all data artifacts, including those captured before a failure.

    A failure to archive is fatal to the job; source files must never silently
    remain outside the database after the worker declares success.
    """
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError('来源快照目录不存在')
    existing = {row.relative_path: row for row in db.scalars(
        select(BriefSourceSnapshot).where(BriefSourceSnapshot.job_id == job.id,
                                          BriefSourceSnapshot.attempt == job.attempts))}
    roots = [('', folder)]
    if history_dir is not None:
        history_dir = Path(history_dir).resolve()
        if history_dir.is_dir():
            roots.append(('history/', history_dir))
    if source_dir is not None:
        source_dir = Path(source_dir).resolve()
        if source_dir != folder:
            if not source_dir.is_dir():
                raise ValueError('被复用的来源快照目录不存在')
            roots.append(('origin/', source_dir))
    total = count = 0
    for prefix, root in roots:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in DATA_EXTENSIONS:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('来源快照路径不安全')
            name = prefix + path.relative_to(root).as_posix()
            if len(name) > 1024 or path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f'来源文件超出入库限制：{name}')
            content = path.read_bytes()
            total += len(content)
            if total > MAX_RUN_BYTES:
                raise ValueError('本次来源数据超出入库限制')
            digest = hashlib.sha256(content).hexdigest()
            prior = existing.get(name)
            if prior:
                if prior.sha256 != digest or prior.content != content:
                    raise ValueError(f'已入库来源快照发生变化：{name}')
                if version is not None:
                    prior.version_id = version.id
            else:
                db.add(BriefSourceSnapshot(team_id=job.team_id, job_id=job.id,
                    version_id=version.id if version is not None else None,
                    attempt=job.attempts, category=job.category,
                    data_date=expected_date(job.category, job.issue_date),
                    relative_path=name, sha256=digest, byte_count=len(content),
                    content=content))
            count += 1
    db.flush()
    return {'storage': 'postgresql', 'job_id': str(job.id), 'attempt': job.attempts,
            'file_count': count, 'total_bytes': total}
