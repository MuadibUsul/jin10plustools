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

from sqlalchemy import select,or_

from app.db.models import BriefSourceSnapshot,BriefSourceBlob
from sqlalchemy.exc import IntegrityError
from .policy import expected_date

DATA_EXTENSIONS = frozenset({'.json', '.csv', '.tsv', '.html', '.xml', '.txt',
                             '.md', '.xls', '.xlsx', '.parquet'})
MAX_FILE_BYTES = 10_000_000
MAX_RUN_BYTES = 100_000_000


def evidence_blob(db,team_id,digest,content):
    blob=db.scalar(select(BriefSourceBlob).where(BriefSourceBlob.team_id==team_id,BriefSourceBlob.sha256==digest))
    if blob is None:
        try:
            with db.begin_nested():
                blob=BriefSourceBlob(team_id=team_id,sha256=digest,byte_count=len(content),content=content)
                db.add(blob);db.flush()
        except IntegrityError:
            blob=db.scalar(select(BriefSourceBlob).where(BriefSourceBlob.team_id==team_id,BriefSourceBlob.sha256==digest))
    if blob is None or blob.byte_count!=len(content) or blob.content!=content:
        raise ValueError('来源证据内容寻址核验失败')
    return blob


def restore_all_a_capture(db,job,folder,expected,previous,*,limit=128):
    """Restore a same-team, exact-session capture with every original page."""
    from .engines.valuation.fetch_free_all_a import SOURCE,load_verified_capture
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    rows=db.scalars(select(BriefSourceSnapshot).where(
        BriefSourceSnapshot.team_id==job.team_id,BriefSourceSnapshot.category=='valuation',
        BriefSourceSnapshot.relative_path.like('%all-a-%-normalized.json')
        ).order_by(BriefSourceSnapshot.captured_at.desc()).limit(limit))
    for snapshot in rows:
        if hashlib.sha256(snapshot.content).hexdigest()!=snapshot.sha256:continue
        payload=json.loads(snapshot.content)
        if (payload.get('source')!=SOURCE or payload.get('expected_date')!=expected
                or payload.get('previous_date_rechecked')!=previous):continue
        original=db.scalar(select(BriefSourceSnapshot).where(
            BriefSourceSnapshot.team_id==job.team_id,BriefSourceSnapshot.category=='valuation',
            BriefSourceSnapshot.sha256==snapshot.sha256).order_by(BriefSourceSnapshot.captured_at).limit(1))
        if original and hashlib.sha256(original.content).hexdigest()==snapshot.sha256:snapshot=original
        prefix=snapshot.relative_path.rsplit('/',1)[0]+'/' if '/' in snapshot.relative_path else ''
        evidence=[];raws=[]
        for item in payload.get('raw_evidence',[]):
            # Captures may have been acquired on Windows before deployment.
            name=str(item['file']).replace('\\','/').rsplit('/',1)[-1]
            source=db.scalar(select(BriefSourceSnapshot).where(
                BriefSourceSnapshot.team_id==job.team_id,BriefSourceSnapshot.job_id==snapshot.job_id,
                BriefSourceSnapshot.attempt==snapshot.attempt,BriefSourceSnapshot.relative_path==prefix+name))
            if source is None or source.sha256!=item['sha256'] or hashlib.sha256(source.content).hexdigest()!=source.sha256:
                evidence=[];break
            path=folder/'all-a-restored-pages'/(source.sha256+'.json')
            evidence.append({**item,'file':str(path.resolve())});raws.append((path,source.content))
        if not evidence:continue
        for path,raw in raws:path.parent.mkdir(exist_ok=True);path.write_bytes(raw)
        restored={**payload,'raw_evidence':evidence,'restored_from_database':True,
            'source_snapshot_id':str(snapshot.id),'original_captured_at':snapshot.captured_at.isoformat()}
        path=folder/'all-a-restored.json'
        path.write_text(json.dumps(restored,ensure_ascii=False),encoding='utf-8')
        load_verified_capture(path,expected,previous)
        return True
    return False


def restore_daily_pe(db,job,folder,expected,*,limit=256,required_dates=None):
    """Recover exact missing PE dates even from jobs that never finished.

    Older workers archived original replies without a successful normalized PE
    pair. Those bytes still contain valid observations and must remain usable.
    """
    from .engines.valuation.fetch_free_pe import observed_values
    from .trading_calendar import previous_session
    from .sources import read,save
    from .data_reliability import save_points
    folder=Path(folder);daily=folder/'pe-published-history.json'
    values={row['date']:row for row in read(daily)} if daily.exists() else {}
    wanted=set(required_dates) if required_dates is not None else {expected,previous_session('xshg',expected).isoformat()}
    if any(day>expected or date.fromisoformat(day).isoformat()!=day for day in wanted):raise ValueError('PE恢复区间不合法')
    available=set()
    for day in wanted & values.keys():
        row=values[day];path=Path(row.get('source_file',''))
        try:
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()==row.get('source_sha256') and str(observed_values(raw).get(day))==row['pe_ttm_mcw_raw']:
                available.add(day)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):pass
    if wanted<=available:return 0
    snapshots=db.scalars(select(BriefSourceSnapshot).where(
        BriefSourceSnapshot.team_id==job.team_id,BriefSourceSnapshot.category=='valuation',
        or_(BriefSourceSnapshot.relative_path.like('%/pe/pe-%-history.json'),
            BriefSourceSnapshot.relative_path.like('%/pe/pe-%-detail.json'),
            BriefSourceSnapshot.relative_path.like('pe-daily/pe-history-%.json'),
            BriefSourceSnapshot.relative_path.like('pe-daily/pe-detail-%.json'))
        ).order_by(BriefSourceSnapshot.captured_at.desc()).limit(limit))
    restored=0
    for snapshot in snapshots:
        if hashlib.sha256(snapshot.content).hexdigest()!=snapshot.sha256:continue
        try:observed=observed_values(snapshot.content)
        except (ValueError,RuntimeError,KeyError,TypeError):continue
        found=(wanted-available)&observed.keys()
        if not found:continue
        # A later job may archive a restored copy of the same original bytes.
        # Preserve the first actual acquisition time rather than the copy time.
        original=db.scalar(select(BriefSourceSnapshot).where(
            BriefSourceSnapshot.team_id==job.team_id,BriefSourceSnapshot.category=='valuation',
            BriefSourceSnapshot.sha256==snapshot.sha256).order_by(BriefSourceSnapshot.captured_at).limit(1))
        if original and hashlib.sha256(original.content).hexdigest()==snapshot.sha256:snapshot=original
        path=folder/'pe-restored'/('pe-'+snapshot.sha256+'.json');path.parent.mkdir(exist_ok=True,parents=True)
        path.write_bytes(snapshot.content)
        points=[]
        for day in found:
            values[day]={'date':day,'pe_ttm_mcw_raw':str(observed[day]),'source_file':str(path.resolve()),'source_sha256':snapshot.sha256}
            points.append({'date':day,'series':'csi300_pe','method':'snowball_fund_danjuan_public','values':values[day]})
        save_points(db,job.team_id,'valuation',points,snapshot.job_id,
                    source_snapshot_id=snapshot.id,collected_at=snapshot.captured_at)
        available.update(found);restored+=len(found)
        save(daily,[values[day] for day in sorted(values)])
        if wanted<=available:break
    return restored


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
                    blob=evidence_blob(db,job.team_id,digest,content)))
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
