import io
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.briefs.manual_compute import normalize, store_manual
from app.briefs.public_compute_store import store_visible
from app.db.models import AIComputeObservation, BriefIssue, BriefVersion, Material
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
    assert keys[-3:] == ['ai_compute_observations', 'token_cost_observations',
                         'gpu_rental_observations']
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


def test_ai_compute_export_separates_briefs_and_uses_current_single_day_values(client, db_factory, seed):
    team_id = seed['admin'].team_id
    token_day = date(2026, 10, 20)
    gpu_day = date(2026, 10, 21)
    with db_factory() as db:
        store_visible(db, team_id, {
            'category': 'token_cost', 'data_date': token_day.isoformat(),
            'captured_at': '2026-10-20T12:00:00Z',
            'readings': [{'metric': 'token', 'date': token_day.isoformat(),
                          'price': '0.97', 'week_change_pct': '-2.1'}],
        })
        store_manual(db, team_id, normalize(
            'token_cost', token_day, token_day, token_day,
            {'token': '0.973456'}, {'token': '-1.82'}, '编辑核对', seed['editor'].id))
        store_visible(db, team_id, {
            'category': 'gpu_rental', 'data_date': gpu_day.isoformat(),
            'captured_at': '2026-10-21T12:00:00Z',
            'readings': [
                {'metric': 'a100', 'date': gpu_day.isoformat(), 'price': '1.52',
                 'week_change_pct': '-0.65'},
                {'metric': 'h100', 'date': gpu_day.isoformat(), 'price': '2.811',
                 'week_change_pct': '0.36'},
                {'metric': 'b200', 'date': gpu_day.isoformat(), 'price': '6.01',
                 'week_change_pct': '2.21'},
            ],
        })
        db.add(AIComputeObservation(
            team_id=seed['outsider'].team_id, kind='token', date=token_day,
            token_price=Decimal('9.99'), source='other team',
            source_status='imported_workbook', revision=1))
        db.commit()

    headers = login(client, 'viewer@plus.test')
    catalog = {item['key']: item for item in client.get('/api/export/datasets', headers=headers).json()['datasets']}
    assert catalog['token_cost_observations']['label'] == 'AI算力CPI·Token价格'
    assert catalog['gpu_rental_observations']['label'] == 'AI景气度指标·GPU租赁价格'

    token_key = 'token_cost_observations'
    params = {'start': token_day.isoformat(), 'end': token_day.isoformat()}
    assert client.get(f'/api/export/{token_key}/count', headers=headers, params=params).json()['count'] == 1
    token = client.get(f'/api/export/{token_key}', headers=headers,
                       params={**params, 'format': 'json'}).json()
    assert len(token) == 1
    assert token[0]['date'] == '2026-10-20'
    assert token[0]['token_price'] == '0.973456'
    assert token[0]['week_change_pct'] == '-1.82'
    assert token[0]['source_status'] == 'manual_entry' and token[0]['revision'] == 2
    assert client.get(f'/api/export/{token_key}', headers=login(client, 'out@plus.test'),
                      params={**params, 'format': 'json'}).json()[0]['token_price'] == '9.99'
    assert client.get(f'/api/export/{token_key}', headers=headers,
                      params={'start': gpu_day.isoformat(), 'end': gpu_day.isoformat(),
                              'format': 'json'}).json() == []

    gpu_key = 'gpu_rental_observations'
    gpu_params = {'start': gpu_day.isoformat(), 'end': gpu_day.isoformat()}
    gpu = client.get(f'/api/export/{gpu_key}', headers=headers,
                     params={**gpu_params, 'format': 'json'}).json()
    assert len(gpu) == 1
    assert {key: gpu[0][key] for key in ('a100', 'h100', 'b200')} == {
        'a100': '1.52', 'h100': '2.811', 'b200': '6.01'}
    assert {key: gpu[0][key] for key in ('a100_week_change_pct', 'h100_week_change_pct',
                                        'b200_week_change_pct')} == {
        'a100_week_change_pct': '-0.65', 'h100_week_change_pct': '0.36',
        'b200_week_change_pct': '2.21'}
    assert client.get(f'/api/export/{gpu_key}', headers=login(client, 'out@plus.test'),
                      params={**gpu_params, 'format': 'json'}).json() == []

    csv_lines = client.get(f'/api/export/{token_key}', headers=headers,
                           params=params).content.decode('utf-8-sig').splitlines()
    assert len(csv_lines) == 2 and '0.973456,-1.82' in csv_lines[1]
    workbook = load_workbook(io.BytesIO(client.get(f'/api/export/{gpu_key}', headers=headers,
                                                 params={**gpu_params, 'format': 'xlsx'}).content),
                             read_only=True)
    sheet_rows = list(workbook['数据'].iter_rows(values_only=True))
    assert len(sheet_rows) == 2
    assert sheet_rows[0][:4] == ('数据日期', 'A100价格', 'H100价格', 'B200价格')
    assert sheet_rows[1][:7] == ('2026-10-21', '1.52', '2.811', '6.01', '-0.65', '0.36', '2.21')
    assert len(client.get('/api/export/ai_compute_observations', headers=headers,
                          params={'format': 'json'}).json()) == 2


def test_ai_compute_export_keeps_workbook_gaps_and_missing_week_change(client, db_factory, seed):
    with db_factory() as db:
        db.add(AIComputeObservation(
            team_id=seed['admin'].team_id, kind='gpu', date=date(2026, 10, 14),
            a100=Decimal('1.520000'), h100=None, b200=Decimal('6.010000'),
            source='Silicon Data workbook', source_status='imported_workbook', revision=1))
        db.commit()
    headers = login(client, 'viewer@plus.test')
    result = client.get('/api/export/gpu_rental_observations', headers=headers,
                        params={'start': '2026-10-14', 'end': '2026-10-14',
                                'format': 'json'}).json()
    assert len(result) == 1
    assert result[0]['a100'] == '1.52' and result[0]['h100'] is None
    assert result[0]['b200'] == '6.01'
    assert all(result[0][f'{metric}_week_change_pct'] is None
               for metric in ('a100', 'h100', 'b200'))
