"""Run inside the API container to check the deployed daily workflow without saving a test issue."""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import httpx
import pymupdf
from pptx import Presentation
from sqlalchemy import select

from app.briefs.policy import CHINA
from app.db.models import Asset, Issue, Material, Role, User
from app.db.session import get_session_factory
from app.schemas import GenerateRequest
from app.security import create_access_token
from app.services.issue_builder import render_issue


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8000'
    with get_session_factory()() as db, httpx.Client(base_url=base, timeout=180) as client:
        for path, marker in (('/', 'portal.js'), ('/daily/', '/app.js'),
                             ('/briefs/', '/briefs.js'), ('/fresh/', 'portal.js'),
                             ('/annotations/', 'portal.js'), ('/settings/', '/settings.js')):
            response = client.get(path)
            assert response.status_code == 200 and marker in response.text, path
            assert '/test/' not in response.text, path
        assert client.get('/test/').status_code == 404
        assert client.get('/test/briefs/').status_code == 404
        print('Public routes OK; test routes removed', flush=True)

        user = db.scalar(select(User).where(User.is_active.is_(True),
                         User.role.in_([Role.ADMIN, Role.EDITOR])))
        assert user is not None, 'No active editor'
        token, _ = create_access_token(user.id, user.team_id)
        client.headers['Authorization'] = 'Bearer ' + token
        day = datetime.now(CHINA).date().isoformat()
        for path in ('/api/auth/me', f'/api/pool?day={day}',
                     f'/api/board?day={day}', '/api/materials/changes?limit=2',
                     '/api/briefs/categories', '/api/settings', f'/api/briefs/issues?date={day}'):
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
        for path, body in (('/api/materials/sync', {'materials': []}),
                           ('/api/assets/exists', {'ids': []})):
            response = client.post(path, json=body)
            assert response.status_code == 200, (path, response.status_code)
        print('Authentication, pool, board, extension sync and briefs APIs OK', flush=True)

        issue = db.scalar(select(Issue).where(Issue.team_id == user.team_id)
                          .order_by(Issue.created_at.desc()))
        assert issue is not None
        for kind, available in (('pptx', issue.pptx_path), ('pdf', issue.pdf_path)):
            if available:
                response = client.get(f'/api/issues/{issue.id}/file?format={kind}')
                assert response.status_code == 200 and len(response.content) > 1000
        asset = db.scalar(select(Asset).where(Asset.team_id == user.team_id))
        if asset:
            response = client.get(f'/api/assets/{asset.id}')
            assert response.status_code == 200 and response.content
        print('Existing daily files and material image downloads OK', flush=True)

        material = db.scalar(select(Material).where(Material.team_id == user.team_id,
                             Material.deleted_at.is_(None)).order_by(Material.collect_time.desc()))
        assert material is not None
        request = GenerateRequest.model_validate({'issue_date': day, 'sections': [
            {'id': material.category or 'macro', 'material_ids': [str(material.id)]}
        ], 'cover_material_ids': []})
        with tempfile.TemporaryDirectory(prefix='plus-release-check-') as tmp:
            pptx, pdf, pages, warnings = render_issue(db, user, request, Path(tmp))
            assert pptx.is_file() and pdf is not None and pdf.is_file(), warnings
            assert len(Presentation(pptx).slides) == pages
            with pymupdf.open(pdf) as document:
                assert len(document) == pages
                assert any(page.get_text().strip() for page in document)
            print(f'Actual LibreOffice export OK: PPTX + PDF, {pages} pages; warnings={warnings}', flush=True)


if __name__ == '__main__':
    main()
