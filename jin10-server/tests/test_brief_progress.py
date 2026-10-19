from app.briefs.progress import record, read
from app.briefs.service import enqueue, job_out, root_for
from datetime import date


def test_progress_survives_api_reload_and_terminal_failure(db_factory, seed):
    with db_factory() as db:
        job = enqueue(db, seed['editor'].team_id, date(2026, 10, 15), 'valuation', user_id=seed['editor'].id)
        db.commit()
        assert job_out(job)['progress']['step'] == 0
        folder = root_for(job.team_id) / 'runs' / str(job.id)
        record(folder, 1, '准备任务')
        record(folder, 2, '正在读取PE原始来源')
        job.status = 'running'
        assert job_out(job)['progress']['events'][-1]['message'] == '正在读取PE原始来源'
        job.status = 'failed'; job.error = 'PE来源缺少交易日'
        data = job_out(job)
        assert data['progress']['step'] == 2
        assert data['progress']['events'][-1]['message'] == job.error
        for i in range(90): record(folder, 2, str(i))
        assert len(read(folder)['events']) == 60
        assert not list(folder.glob('*.tmp'))
