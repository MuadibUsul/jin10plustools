import io
import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from app.briefs.engines.valuation import run_erp


def report_pdf(day='2026/11/12', close='4043.93'):
    report = pymupdf.open()
    cover = report.new_page()
    cover.insert_text((40, 40), f'CSI daily report {day}')
    page = report.new_page()
    page.insert_text((40, 40), '\n'.join([
        '000300', 'CSI 300', '20041231 20050408 1000',
        '508424', '230145', close, '-0.78%',
    ]))
    data = report.tobytes()
    report.close()
    return data


def test_missing_live_close_uses_dated_official_report(tmp_path, monkeypatch):
    perf = json.dumps({'code': '200', 'success': True, 'data': [
        {'indexCode': '000300', 'tradeDate': '20261111', 'close': '4075.71'},
    ]}).encode()
    report = report_pdf()

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    def source(request, timeout):
        return Reply(report if 'csiindexdailyreport20261112.pdf' in request.full_url else perf)

    monkeypatch.setattr(run_erp, 'urlopen', source)
    result = run_erp.fetch_close('2026-11-12', '2026-11-11', tmp_path)
    assert [(row['date'], row['close'], row['source']) for row in result['rows']] == [
        ('2026-11-11', '4075.71', 'CSI_official_index_performance'),
        ('2026-11-12', '4043.93', 'CSI_official_daily_report'),
    ]
    assert len(result['daily_reports']) == 1
    evidence = result['daily_reports'][0]['raw_evidence']
    assert evidence['sha256'] == hashlib.sha256(report).hexdigest()
    assert Path(evidence['path']).read_bytes() == report


def test_daily_report_must_identify_requested_session():
    with pytest.raises(run_erp.ERPWorkflowError, match='does not identify 2026-11-12'):
        run_erp._daily_report_close(report_pdf(day='2026/11/10'), '2026-11-12')
