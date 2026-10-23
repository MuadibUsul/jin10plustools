"""Persist each brief job's original and normalized data in PostgreSQL.

The filesystem remains the rendering workspace.  Database copies are keyed by
job, attempt and relative path, and retain the exact bytes with a digest.
Images are handled separately by the image-delivery contract.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db.models import BriefSourceSnapshot
from .policy import expected_date

DATA_EXTENSIONS = frozenset({'.json', '.csv', '.tsv', '.html', '.xml', '.txt',
                             '.md', '.xls', '.xlsx', '.parquet'})
MAX_FILE_BYTES = 10_000_000
MAX_RUN_BYTES = 100_000_000


def archive_run(db, job, folder: Path, *, history_dir: Path | None = None,
                source_dir: Path | None = None, version=None, data_date: str | None = None):
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
            if not path.is_file() or path.suffix.lower() not in DATA_EXTENSIONS or path.name == 'progress.json':
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
                    data_date=date.fromisoformat(data_date) if data_date else expected_date(job.category, job.issue_date),
                    relative_path=name, sha256=digest, byte_count=len(content),
                    content=content))
            count += 1
    db.flush()
    return {'storage': 'postgresql', 'job_id': str(job.id), 'attempt': job.attempts,
            'file_count': count, 'total_bytes': total}


def restore_pe_capture(db, job, folder: Path, expected_date: str) -> bool:
    """Restore a dated, byte-verified PE capture when the live history drops it.

    The provider's long history is sampled weekly. A capture made while the
    requested session was current is still valid historical evidence, but must
    be identified as a database reuse rather than a new network observation.
    """
    snapshots = db.scalars(select(BriefSourceSnapshot).where(
        BriefSourceSnapshot.team_id == job.team_id,
        BriefSourceSnapshot.category == 'valuation',
        BriefSourceSnapshot.data_date <= date.fromisoformat(expected_date),
        BriefSourceSnapshot.relative_path.like('%raw/pe/%-normalized.json'),
    ).order_by(BriefSourceSnapshot.data_date.desc(),BriefSourceSnapshot.captured_at.desc()).limit(64)).all()
    restored = 0
    for snapshot in snapshots:
        if hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256:
            continue
        try:
            parsed = json.loads(snapshot.content)
            if (parsed.get('source') != 'snowball_fund_danjuan_public'
                    or parsed.get('expected_date', '9999') > expected_date
                    or {r['date'] for r in parsed['rows']} !=
                       {parsed['expected_date'], parsed['previous_date_rechecked']}):
                continue
            evidence = parsed['raw_evidence']
            if not evidence:
                continue
            prefix = snapshot.relative_path.rsplit('/', 1)[0] + '/'
            raws = []
            for item in evidence:
                name = Path(item['file']).name
                if not name.startswith('pe-') or not name.endswith('.json'):
                    raise ValueError('Unexpected PE evidence filename')
                source = db.scalars(select(BriefSourceSnapshot).where(
                    BriefSourceSnapshot.job_id == snapshot.job_id,
                    BriefSourceSnapshot.attempt == snapshot.attempt,
                    BriefSourceSnapshot.relative_path == prefix + name)).first()
                if (source is None or source.sha256 != item['sha256']
                        or hashlib.sha256(source.content).hexdigest() != item['sha256']):
                    raise ValueError('PE evidence digest mismatch')
                raws.append((name, source.content))
        except (KeyError, TypeError, ValueError, UnicodeError):
            continue
        target = Path(folder) / 'erp' / 'raw' / 'pe'
        target.mkdir(parents=True, exist_ok=True)
        if any((target/name).exists() and (target/name).read_bytes() != content for name,content in raws):
            continue
        for name, content in raws:
            (target / name).write_bytes(content)
        for item in evidence:
            item['file'] = str((target / Path(item['file']).name).resolve())
        parsed['restored_from_database'] = True
        parsed['database_source_job_id'] = str(snapshot.job_id)
        (target / Path(snapshot.relative_path).name).write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2), encoding='utf-8')
        restored += 1
        if restored >= 8:
            break
    return bool(restored)


def import_pe_capture(db, job, normalized_path):
    """Import only verified PE evidence into an existing matching failed job."""
    from app.briefs.engines.valuation.fetch_free_pe import verified_capture
    normalized_path = Path(normalized_path)
    capture = verified_capture(normalized_path)
    if capture is None or job.category != 'valuation' or expected_date(job.category, job.issue_date).isoformat() != capture['expected_date']:
        raise ValueError('PE快照未通过原始证据校验，或与目标任务的数据日期不匹配')
    original = normalized_path.read_bytes()
    prefix = 'recovered-pe/' + hashlib.sha256(original).hexdigest()[:16] + '/erp/raw/pe/'
    files = [(normalized_path.name, original)] + [(Path(item['file']).name, Path(item['file']).read_bytes())
                                                for item in capture['raw_evidence']]
    for name, content in files:
        path = prefix + name
        prior = db.scalar(select(BriefSourceSnapshot).where(BriefSourceSnapshot.job_id == job.id,
                          BriefSourceSnapshot.attempt == job.attempts, BriefSourceSnapshot.relative_path == path))
        if prior is not None:
            if prior.content != content:
                raise ValueError('已导入的PE证据发生变化')
            continue
        db.add(BriefSourceSnapshot(team_id=job.team_id, job_id=job.id, attempt=job.attempts,
            category='valuation', data_date=date.fromisoformat(capture['expected_date']), relative_path=path,
            sha256=hashlib.sha256(content).hexdigest(), byte_count=len(content), content=content))
    db.flush()
    return len(files)
