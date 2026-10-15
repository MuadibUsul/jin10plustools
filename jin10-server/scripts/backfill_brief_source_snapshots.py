"""Backfill surviving brief data files into the source-snapshot table.

Run only after migration 0010. Existing run folders do not identify which retry
created each file, so legacy files are assigned to the job's final attempt. The
current mutable history directory is attached only to the latest job in its
team/category; it is a backfill-time snapshot, not a claim about an earlier run.
"""
import argparse

from sqlalchemy import select

from app.briefs.service import root_for
from app.briefs.source_archive import archive_run
from app.db.models import BriefJob, BriefVersion
from app.db.session import get_session_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write snapshots; default is a dry run.')
    args = parser.parse_args()
    factory = get_session_factory()
    with factory() as db:
        jobs = list(db.scalars(select(BriefJob).order_by(BriefJob.created_at, BriefJob.id)))
        versions = list(db.scalars(select(BriefVersion).order_by(BriefVersion.number)))
        by_run = {v.payload.get('artifact_dir'): v for v in versions
                  if isinstance(v.payload, dict) and v.payload.get('artifact_dir')}
        latest = {(j.team_id, j.category): j.id for j in jobs}
        jobs_with_files = rows = total_bytes = 0
        for job in jobs:
            root = root_for(job.team_id)
            folder = root / 'runs' / str(job.id)
            if not folder.is_dir():
                continue
            if job.attempts < 1:
                raise ValueError(f'Run folder has no recorded job attempt: {job.id}')
            version = by_run.get(f'runs/{job.id}')
            history = (root / 'history' / job.category) if latest[(job.team_id, job.category)] == job.id else None
            if args.apply:
                manifest = archive_run(db, job, folder, history_dir=history, version=version)
                db.commit()
                count, byte_count = manifest['file_count'], manifest['total_bytes']
            else:
                from app.briefs.source_archive import DATA_EXTENSIONS
                paths = [p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in DATA_EXTENSIONS]
                if history and history.is_dir():
                    paths += [p for p in history.rglob('*') if p.is_file() and p.suffix.lower() in DATA_EXTENSIONS]
                count, byte_count = len(paths), sum(p.stat().st_size for p in paths)
            jobs_with_files += 1
            rows += count
            total_bytes += byte_count
        if not args.apply:
            db.rollback()
        print({'applied': args.apply, 'jobs': jobs_with_files, 'files': rows, 'bytes': total_bytes})


if __name__ == '__main__':
    main()
