"""Additive local working series. PostgreSQL revisions are saved by the worker."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path


def merge_history(path, rows, *, method, key='date'):
    from .sources import read, save
    path=Path(path)
    before=read(path) if path.is_file() else {'method':method,'rows':[]}
    if not isinstance(before,dict) or before.get('method')!=method:
        raise ValueError('历史来源或计算方法不同，禁止直接拼接覆盖')
    incoming={}
    for raw in rows:
        row=deepcopy(raw); stamp=date.fromisoformat(row[key]).isoformat()
        if stamp in incoming: raise ValueError('新历史数据日期重复')
        incoming[stamp]=row
    merged={row[key]:row for row in before['rows']}
    merged.update(incoming)
    output=[merged[stamp] for stamp in sorted(merged)]
    save(path,{'method':method,'rows':output})
    return output
