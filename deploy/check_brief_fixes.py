"""Compare archived delivery bytes before/after the turnover-only policy update."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

import httpx
import numpy as np
from PIL import Image
from sqlalchemy import select

from app.briefs.service import artifact_path, delivered_image
from app.db.models import BriefIssue, BriefVersion, User, Role
from app.db.session import get_session_factory
from app.security import create_access_token


mode, report_path = sys.argv[1:3]
report_path = Path(report_path)
before = json.loads(report_path.read_text()) if mode != 'before' else {}
after = {}
turnover_count = 0
with get_session_factory()() as db:
    for issue in db.scalars(select(BriefIssue)):
        for version in db.scalars(select(BriefVersion).where(BriefVersion.issue_id == issue.id)):
            for name in version.payload.get('images', []):
                source = artifact_path(issue, version, name)
                delivered, _ = delivered_image(issue, version, name)
                key = str(version.id) + '/' + name
                after[key] = {'category': issue.category,
                              'source': hashlib.sha256(source.read_bytes()).hexdigest(),
                              'delivery': hashlib.sha256(delivered.read_bytes()).hexdigest()}
                if mode == 'before':
                    continue
                assert after[key]['source'] == before[key]['source'], 'Original snapshot changed'
                if issue.category != 'turnover':
                    assert after[key]['delivery'] == before[key]['delivery'], 'Unrelated delivery changed'
                    continue
                with Image.open(delivered) as image, Image.open(source) as original:
                    assert image.size == (1118, 1514)
                    region = np.asarray(image.convert('RGB'))[1318:1361, 765:1068].astype(int)
                    purple = ((region[:, :, 2] > 60) & (region[:, :, 2] > region[:, :, 0] * 1.6)
                              & (region[:, :, 2] > region[:, :, 1] * 1.3))
                    assert not purple.any(), 'Embedded old logo remains'
                assert delivered.stat().st_size <= 266000
                assert after[key]['delivery'] != before[key]['delivery']
                turnover_count += 1
                if mode == 'public':
                    user = db.scalar(select(User).where(User.team_id == issue.team_id,
                                     User.is_active.is_(True), User.role.in_([Role.ADMIN, Role.EDITOR])))
                    token, _ = create_access_token(user.id, user.team_id)
                    response = httpx.get(f'https://plus.tlines.tech/api/briefs/issues/{issue.id}/files/{version.number}/{name}',
                                         headers={'Authorization': 'Bearer ' + token}, timeout=120)
                    assert response.status_code == 200
                    assert response.content == delivered.read_bytes()
                if turnover_count == 1:
                    shutil.copyfile(delivered, report_path.parent / ('fixed-turnover' + delivered.suffix))
                print(f'Turnover v{version.number}: {delivered.stat().st_size} bytes; old logo removed; original preserved', flush=True)
if mode == 'before':
    report_path.write_text(json.dumps(after, ensure_ascii=False))
    print(f'Captured {len(after)} existing image deliveries', flush=True)
else:
    assert set(after) == set(before), 'Editions changed during comparison'
    assert turnover_count > 0
    print(f'Checked {len(after)} images, {turnover_count} turnover editions; all other delivery bytes unchanged', flush=True)
