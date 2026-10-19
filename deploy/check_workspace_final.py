"""Verify completed real jobs, public downloads, immutable originals and daily settings."""
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

import httpx
import numpy as np
from PIL import Image
from sqlalchemy import select

from app.briefs.service import artifact_path, delivered_image, find_issue, job_out, root_for
from app.db.models import BriefJob, BriefSourceSnapshot, BriefVersion, User, Role
from app.db.session import get_session_factory
from app.security import create_access_token
from app.services.image_delivery import download_name

factory = get_session_factory()
jobs = json.loads(Path('/data/repair-jobs-20261016.json').read_text())
states = {}
deadline = time.monotonic() + 600
while True:
    terminal = True
    with factory() as db:
        for item in jobs:
            job = db.get(BriefJob, uuid.UUID(item['id']))
            state = job_out(job)
            mark = (state['status'], state['progress']['step'])
            if states.get(item['id']) != mark:
                print(item['day'], item['category'], *mark, state['error'] or '', flush=True)
                states[item['id']] = mark
            if job.status in ('queued', 'running', 'waiting'):
                terminal = False
    if terminal:
        break
    assert time.monotonic() < deadline, 'Timed out waiting for real generation jobs'
    time.sleep(3)

with factory() as db, httpx.Client(base_url='https://plus.tlines.tech', timeout=120) as client:
    for item in jobs:
        job = db.get(BriefJob, uuid.UUID(item['id']))
        assert job.status == 'succeeded', (item, job.error)
        user = db.scalar(select(User).where(User.team_id == job.team_id, User.is_active.is_(True), User.role.in_([Role.ADMIN, Role.EDITOR])))
        token, _ = create_access_token(user.id, user.team_id)
        client.headers['Authorization'] = 'Bearer ' + token
        response = client.get('/api/briefs/jobs/' + str(job.id))
        assert response.status_code == 200 and response.json()['progress']['step'] == 6
        issue = find_issue(db, job.team_id, job.issue_date, job.category)
        version = db.scalar(select(BriefVersion).where(BriefVersion.issue_id == issue.id, BriefVersion.number == issue.version))
        assert version.payload['valid']
        count = len(db.scalars(select(BriefSourceSnapshot.id).where(BriefSourceSnapshot.job_id == job.id)).all())
        assert count > 0
        if job.category == 'liquidity':
            folder = root_for(job.team_id) / 'runs' / str(job.id)
            audit = json.loads((folder / 'chart-audit.json').read_text())
            assert len(audit['table_columns']) == 4 and all(label['inline'] for label in audit['explanation_inline_labels'])
            assert len(version.payload['images']) == 1
        for name in version.payload['images']:
            delivered, _ = delivered_image(issue, version, name)
            assert delivered.stat().st_size <= 266000
            response = client.get(f'/api/briefs/issues/{issue.id}/files/{version.number}/{name}')
            assert response.status_code == 200 and response.content == delivered.read_bytes()
            assert download_name(issue.issue_date, version.title, delivered.suffix) in unquote(response.headers['content-disposition'])
            print(item['day'], job.category, 'public image OK', delivered.stat().st_size, 'bytes', 'source files', count, flush=True)
        assert client.get('/api/settings').status_code == 200
    # Compare old editions by identity; newly generated editions are expected additions.
    baseline = json.loads(Path('/baseline/before.json').read_text())
    preserved = 0
    for version in db.scalars(select(BriefVersion)):
        from app.db.models import BriefIssue
        issue = db.get(BriefIssue, version.issue_id)
        for name in version.payload.get('images', []):
            key = str(version.id) + '/' + name
            if key not in baseline:
                continue
            original = artifact_path(issue, version, name)
            assert hashlib.sha256(original.read_bytes()).hexdigest() == baseline[key]['source']
            final, _ = delivered_image(issue, version, name)
            if issue.category != 'turnover':
                assert hashlib.sha256(final.read_bytes()).hexdigest() == baseline[key]['delivery']
            else:
                with Image.open(final) as image:
                    region = np.asarray(image.convert('RGB'))[1318:1361, 765:1068].astype(int)
                    assert not ((region[:,:,2] > 60) & (region[:,:,2] > region[:,:,0]*1.6) & (region[:,:,2] > region[:,:,1]*1.3)).any()
            preserved += 1
    assert preserved == len(baseline)
    print('All', preserved, 'historical originals preserved; unrelated historical deliveries unchanged; turnover logo regression passed', flush=True)
    for url, marker in [('/briefs.js', '所有栏目处理动态'), ('/workspace.js', '/settings/'), ('/daily/', '/daily.css'), ('/settings/', '/settings.js'), ('/retro-terminal.css', 'matrix-fall')]:
        response = client.get(url)
        assert response.status_code == 200 and marker in response.text
        assert response.headers.get('cache-control') == 'no-cache'
    script=client.get('/briefs.js').text
    assert 'process-category' not in script and 'event.message' not in script
    response=client.get('/assets/retro-computer-frame.png')
    assert response.status_code==200 and response.headers['content-type'].startswith('image/png')
    import io
    with Image.open(io.BytesIO(response.content)) as frame:
        assert frame.mode=='RGBA' and frame.getpixel((frame.width//2,frame.height//3))[3]==0
    print('Public workspace UI, shared settings, live terminal, transparent screen and no-cache assets OK', flush=True)
