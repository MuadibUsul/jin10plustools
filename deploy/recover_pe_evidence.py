"""One-time, idempotent import of verified original PE bytes; never imports a database."""
from pathlib import Path
from app.db.models import BriefJob
from app.db.session import get_session_factory
from app.briefs.source_archive import import_pe_capture
import uuid

path = Path('/archive/storage/e3d2d207-ea80-40ef-b8c6-8121893f55a5/briefs/runs/8b9b9113-a9ed-45cd-b7ae-8fd82aab648f/erp/raw/pe/pe-verified-import-normalized.json')
with get_session_factory()() as db:
    job = db.get(BriefJob, uuid.UUID('c2cd8222-e476-4d73-ba65-ee5cc3ac9e36'))
    assert job and job.status == 'failed' and str(job.team_id) == 'e3d2d207-ea80-40ef-b8c6-8121893f55a5'
    count = import_pe_capture(db, job, path)
    db.commit()
    print(f'Imported {count} verified PE evidence files only; existing failed job and editions unchanged', flush=True)
