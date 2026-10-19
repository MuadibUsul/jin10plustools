"""Bounded, atomic task progress shared by the worker, runner and API."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

TOTAL_STEPS = 6


def record(folder, step, message):
    path = Path(folder)/'progress.json'
    data = read(folder)
    events = data.get('events', [])
    events.append({'at': datetime.now(timezone.utc).isoformat(), 'message': message[:500]})
    data = {'step': step, 'total': TOTAL_STEPS, 'label': message[:500], 'events': events[-60:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('progress.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def read(folder):
    path = Path(folder)/'progress.json'
    try:
        if path.stat().st_size > 64_000:
            return {}
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def for_job(job, root):
    data = read(Path(root)/'runs'/str(job.id))
    label = {'queued': '等待执行', 'running': '正在执行', 'waiting': '等待来源数据更新',
             'succeeded': '生成完成', 'failed': '生成失败', 'skipped': '任务已停止'}.get(job.status, job.status)
    if not data:
        data = {'step': 0, 'total': TOTAL_STEPS, 'label': label,
                'events': [{'at': job.created_at.isoformat(), 'message': '任务已加入队列'}]}
    if job.status == 'succeeded':
        data['step'] = TOTAL_STEPS
    if job.status != 'running':
        data['label'] = label
    if job.error and (not data['events'] or data['events'][-1]['message'] != job.error):
        data['events'].append({'at': job.updated_at.isoformat(), 'message': job.error})
    return data
