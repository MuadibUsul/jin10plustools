"""Export of stored team data by date range for every signed-in member."""
from __future__ import annotations

from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from app.deps import CurrentUser, DbSession
from app.services import data_export

router = APIRouter(prefix='/api/export', tags=['export'])

MEDIA = {
    'csv': 'text/csv; charset=utf-8',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'json': 'application/json; charset=utf-8',
}


def dataset_or_404(key: str) -> data_export.Dataset:
    dataset = data_export.DATASETS.get(key)
    if dataset is None:
        raise HTTPException(404, '未找到该数据集')
    return dataset


def check_range(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise HTTPException(422, '开始日期不能晚于结束日期')


@router.get('/datasets')
def datasets(user: CurrentUser):
    return {
        'max_rows': data_export.MAX_ROWS,
        'datasets': [{
            'key': item.key, 'label': item.label, 'description': item.description,
            'date_label': item.date_label, 'columns': [column.label for column in item.columns],
        } for item in data_export.DATASETS.values()],
    }


@router.get('/{key}/count')
def count(key: str, db: DbSession, user: CurrentUser,
          start: date | None = None, end: date | None = None):
    dataset = dataset_or_404(key)
    check_range(start, end)
    return {'count': dataset.count(db, user.team_id, start, end), 'max_rows': data_export.MAX_ROWS}


@router.get('/{key}')
def export(key: str, db: DbSession, user: CurrentUser,
           start: date | None = None, end: date | None = None,
           fmt: Annotated[str, Query(alias='format')] = 'csv'):
    dataset = dataset_or_404(key)
    check_range(start, end)
    if fmt not in MEDIA:
        raise HTTPException(422, '不支持的导出格式')
    total = dataset.count(db, user.team_id, start, end)
    if total > data_export.MAX_ROWS:
        raise HTTPException(413, f'本次结果 {total} 行，超过 {data_export.MAX_ROWS} 行上限，请缩小时间范围')
    rows = dataset.rows(db, user.team_id, start, end)
    users = data_export.user_map(db, user.team_id)
    writer = {'csv': data_export.csv_bytes, 'json': data_export.json_bytes,
              'xlsx': data_export.xlsx_bytes}[fmt]
    body = writer(dataset, rows, users)
    span = f'{start:%Y-%m-%d}_{end:%Y-%m-%d}' if start and end else '全部已存'
    download = f'PLUS_{dataset.label}_{span}.{fmt}'
    fallback = f'plus-{dataset.key}-{start or "all"}-{end or "all"}.{fmt}'
    disposition = f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(download)}"
    return Response(body, media_type=MEDIA[fmt],
                    headers={'Content-Disposition': disposition,
                             'Content-Length': str(len(body)), 'Cache-Control': 'no-store'})
