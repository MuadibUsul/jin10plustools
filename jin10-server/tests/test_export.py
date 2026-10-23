import io
import json
import uuid
from datetime import date, datetime, timezone

import pytest
from openpyxl import load_workbook

from app.db.models import BriefIssue, BriefVersion, Material
from app.services import data_export
from tests.conftest import login


def material(team_id, title, collected, **extra):
    return Material(id=uuid.uuid4(), team_id=team_id, fingerprint=title, item_kind='flash', title=title,
                    content=f'{title} 正文', content_raw=f'{title} raw', url=f'https://example.com/{title}',
                    raw_html='', collect_time=collected, collect_method='manual',
                    interpretation_status='pending', **extra)


@pytest.fixture
def rows(db_factory, seed):
    admin = seed['admin']
    inside = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)   # 2026-10-16 01:00 Beijing
    outside = datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc)    # 2026-09-30 12:00 Beijing
    with db_factory() as db:
        db.add_all([
            material(admin.team_id, '甲', inside, created_by=admin.id),
            material(admin.team_id, '乙', outside),
            Material(id=uuid.uuid4(), team_id=seed['outsider'].team_id, fingerprint='外部', item_kind='flash',
                     title='外部', content='外部正文', content_raw='', url='https://example.com/out', raw_html='',
                     collect_time=inside, collect_method='manual', interpretation_status='pending'),
        ])
        issue = BriefIssue(team_id=admin.team_id, issue_date=date(2026, 10, 16), category='volume', version=2)
        old = BriefIssue(team_id=admin.team_id, issue_date=date(2026, 10, 2), category='volume', version=1)
        db.add_all([issue, old])
        db.flush()
        db.add_all([
            BriefVersion(issue_id=issue.id, number=2, title='本期', dynamic_body='正文',
                         payload={'fixed_notes': '注释', 'images': ['a.png']}, created_by=admin.id),
            BriefVersion(issue_id=old.id, number=1, title='上期', dynamic_body='正文', payload={}),
        ])
        db.commit()


def test_export_is_open_to_every_signed_in_member(client, rows):
    assert client.get('/api/export/datasets').status_code == 401
    for email in ('admin@plus.test', 'editor@plus.test', 'viewer@plus.test'):
        assert client.get('/api/export/datasets', headers=login(client, email)).status_code == 200
    data = client.get('/api/export/datasets', headers=login(client, 'viewer@plus.test')).json()
    assert data['max_rows'] == data_export.MAX_ROWS
    keys = [item['key'] for item in data['datasets']]
    assert keys[:2] == ['materials', 'assets'] and 'brief_versions' in keys and len(keys) == 27
    assert 'feedback' in keys and 'news_events' in keys
    assert keys[-1] == 'ai_compute_observations'
    labels = {item['key']: item for item in data['datasets']}
    assert labels['materials']['date_label'] == '采集时间'
    assert '标题' in labels['materials']['columns']
    editor = login(client, 'editor@plus.test')
    body = client.get('/api/export/materials', headers=editor,
                      params={'start': '2026-10-16', 'end': '2026-10-16', 'format': 'json'}).json()
    assert [item['title'] for item in body] == ['甲']
    viewer = login(client, 'viewer@plus.test')
    assert client.get('/api/export/materials/count', headers=viewer,
                      params={'start': '2026-10-16', 'end': '2026-10-16'}).json()['count'] == 1
    # A member of another team can export, but only sees that team's rows.
    outsider = login(client, 'out@plus.test')
    other = client.get('/api/export/materials', headers=outsider,
                       params={'start': '2026-10-16', 'end': '2026-10-16', 'format': 'json'}).json()
    assert [item['title'] for item in other] == ['外部']


def test_csv_filters_by_beijing_day_and_team(client, rows):
    headers = login(client, 'admin@plus.test')
    response = client.get('/api/export/materials', headers=headers,
                          params={'start': '2026-10-16', 'end': '2026-10-16'})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/csv')
    assert 'PLUS_%E7%B4%A0' in response.headers['content-disposition']  # 素材库 filename*
    text = response.content.decode('utf-8-sig')
    assert text.splitlines()[0].startswith('素材ID,标题,')
    assert '甲' in text and '乙' not in text and '外部' not in text
    assert '管理员' in text  # created_by resolved to the member name
    assert 'password' not in text.lower()
    # The same Beijing day maps to the previous UTC evening, so the surrounding day must be empty.
    assert client.get('/api/export/materials', headers=headers,
                      params={'start': '2026-09-30', 'end': '2026-09-30'}).content.decode('utf-8-sig').count('\n') == 2


def test_json_and_xlsx_formats(client, rows):
    headers = login(client, 'admin@plus.test')
    payload = client.get('/api/export/materials', headers=headers,
                         params={'start': '2026-10-16', 'end': '2026-10-16', 'format': 'json'}).json()
    assert len(payload) == 1 and payload[0]['title'] == '甲'
    assert payload[0]['collect_time'] == '2026-10-16 01:00:00'
    assert payload[0]['created_by'] == '管理员'
    assert payload[0]['tags'] == []  # structured values stay structured in JSON

    book = load_workbook(io.BytesIO(client.get('/api/export/brief_versions', headers=headers,
                                               params={'start': '2026-10-16', 'end': '2026-10-16',
                                                       'format': 'xlsx'}).content), read_only=True)
    sheet = book['数据']
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][:5] == ('版本ID', '期次ID', '版本号', '生产日期', '版本类型')
    assert len(rows) == 2 and rows[1][2] == 2 and rows[1][3] == '2026-10-16'
    assert json.loads(rows[1][7]) == {'fixed_notes': '注释', 'images': ['a.png']}


def test_brief_versions_filter_by_issue_date_and_skip_other_periods(client, rows):
    headers = login(client, 'admin@plus.test')
    body = client.get('/api/export/brief_versions', headers=headers,
                      params={'start': '2026-10-16', 'end': '2026-10-16', 'format': 'json'}).json()
    assert [item['title'] for item in body] == ['本期']
    older = client.get('/api/export/brief_versions', headers=headers,
                       params={'start': '2026-09-01', 'end': '2026-10-02', 'format': 'json'}).json()
    assert [item['title'] for item in older] == ['上期']


def test_count_range_and_errors(client, rows, monkeypatch):
    headers = login(client, 'admin@plus.test')
    assert client.get('/api/export/materials/count', headers=headers,
                      params={'start': '2026-10-16', 'end': '2026-10-16'}).json()['count'] == 1
    assert client.get('/api/export/materials', headers=headers,
                      params={'start': '2026-10-16', 'end': '2026-10-15'}).status_code == 422
    assert client.get('/api/export/nope', headers=headers).status_code == 404
    assert client.get('/api/export/materials', headers=headers, params={'format': 'xml'}).status_code == 422
    monkeypatch.setattr(data_export, 'MAX_ROWS', 1)
    assert client.get('/api/export/materials', headers=headers).status_code == 413


def test_valuation_series_exports_five_numeric_columns_and_team_dates(client, db_factory, seed, tmp_path):
    from app.briefs.valuation_observations import save_history

    source = tmp_path / 'erp-plot-data.json'
    source.write_text(json.dumps([
        {'date': '2026-10-19', 'close': '4500.25', 'pe_ttm_mcw': '12.5',
         'bond_yield_number': '2.0', 'source_status': 'verified'},
        {'date': '2026-10-20', 'close': '4510.5', 'pe_ttm_mcw': '10',
         'bond_yield_number': '2.0', 'source_status': 'verified'},
    ]), encoding='utf-8')
    with db_factory() as db:
        assert save_history(db, seed['admin'].team_id, None, source) == 2
        db.commit()

    headers = login(client, 'viewer@plus.test')
    key = 'valuation_observations'
    dataset = {item['key']: item for item in client.get('/api/export/datasets', headers=headers).json()['datasets']}[key]
    assert dataset['columns'] == ['日期', '沪深300收盘价', 'PE-TTM市值加权',
                                  '中国十年期国债收益率（不需加百分号）', '沪深300风险溢价指数']
    params = {'start': '2026-10-20', 'end': '2026-10-20'}
    assert client.get(f'/api/export/{key}/count', headers=headers, params=params).json()['count'] == 1
    payload = client.get(f'/api/export/{key}', headers=headers,
                         params={**params, 'format': 'json'}).json()
    assert payload == [{'date': '2026-10-20', 'close': 4510.5, 'pe_ttm_mcw': 10.0,
                        'bond_yield_number': 2.0, 'erp': 0.08}]
    csv_text = client.get(f'/api/export/{key}', headers=headers, params=params).content.decode('utf-8-sig')
    assert csv_text.splitlines()[0] == ','.join(dataset['columns'])
    assert csv_text.splitlines()[1].startswith('2026-10-20,4510.500000,10.000000,2.000000,0.080000000000')
    book = load_workbook(io.BytesIO(client.get(f'/api/export/{key}', headers=headers,
                                                 params={**params, 'format': 'xlsx'}).content), read_only=True)
    lines = list(book['数据'].iter_rows(values_only=True))
    assert lines[0] == tuple(dataset['columns'])
    assert lines[1] == ('2026-10-20', 4510.5, 10, 2, 0.08)
    assert client.get(f'/api/export/{key}', headers=login(client, 'out@plus.test'),
                      params={**params, 'format': 'json'}).json() == []
