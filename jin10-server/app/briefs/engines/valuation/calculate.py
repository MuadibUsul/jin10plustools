"""ERP calculations and auditable prior-session revisions, using Decimal."""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

FIELDS = ('close', 'pe_ttm_mcw', 'bond_yield_number')
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def number(value, name):
    if isinstance(value, bool) or (isinstance(value, str) and '%' in value):
        raise ValueError(f'{name} must be a finite numeric value without a percent sign')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{name} must be a finite numeric value') from None
    if not result.is_finite():
        raise ValueError(f'{name} must be finite')
    return result


def calculate_row(row):
    day = date.fromisoformat(row['date']).isoformat()
    close = number(row['close'], 'close')
    pe = number(row.get('pe_ttm_mcw', row.get('pe_ttm')), 'pe_ttm_mcw')
    bond = number(row['bond_yield_number'], 'bond_yield_number')
    if close <= 0 or pe <= 0 or not -10 <= bond <= 30:
        raise ValueError(f'Inputs out of range for {day}')
    erp = Decimal(1)/pe-bond/100
    return {**row, 'date': day, 'close': str(close), 'pe_ttm_mcw': str(pe),
            'bond_yield_number': str(bond), 'erp': str(erp), 'erp_percent': str(erp*100)}


def validate_history(rows):
    result = [calculate_row(row) for row in rows]
    days = [row['date'] for row in result]
    if len(set(days)) != len(days):
        raise ValueError('Duplicate ERP dates are not allowed')
    return sorted(result, key=lambda row: row['date'])


def read_workbook(path):
    """Read only: extract A:E without changing the source workbook or its formulas."""
    rows = []
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        for xml_row in root.findall('.//s:sheetData/s:row', NS):
            cells = {cell.attrib['r'].rstrip('0123456789'): cell for cell in xml_row.findall('s:c', NS)}
            if not all(column in cells for column in ('A', 'B', 'C', 'D', 'E')):
                continue
            def value(column):
                node = cells[column].find('s:v', NS)
                return node.text if node is not None else None
            if value('A') is None or cells['A'].attrib.get('t') == 's':
                continue
            day = (date(1899, 12, 30)+timedelta(days=int(Decimal(value('A'))))).isoformat()
            rows.append({'date': day, 'close': value('B'), 'pe_ttm_mcw': value('C'),
                         'bond_yield_number': value('D'), 'erp_cached': value('E'),
                         'excel_row': int(xml_row.attrib['r']), 'source_status': 'uploaded_workbook_unverified_online'})
    calculated = validate_history(rows)
    mismatches = [r['date'] for r in calculated if r.get('erp_cached') is not None
                  and abs(Decimal(r['erp'])-Decimal(r['erp_cached'])) > Decimal('1e-12')]
    if mismatches:
        raise ValueError(f'Workbook ERP caches disagree with the documented formula: {mismatches[:5]}')
    return calculated


def load_history(path):
    if Path(path).suffix.lower() == '.xlsx':
        return read_workbook(path)
    return validate_history(json.loads(Path(path).read_text(encoding='utf-8-sig')))


def apply_observations(history, observations, expected_date):
    """Require new + previous source sessions, preserving revision evidence.

    The caller owns source/method compatibility. This function does not declare
    a free alternative equivalent to the original PE or bond definition.
    """
    old = {r['date']: r for r in validate_history(history)}
    expected = date.fromisoformat(expected_date).isoformat()
    previous_days = [d for d in old if d < expected]
    if not previous_days:
        raise ValueError('A previous source session is required for revision checking')
    previous = max(previous_days)
    fresh = validate_history(observations)
    incoming = {r['date']: r for r in fresh}
    if not {previous, expected} <= set(incoming):
        raise ValueError('Observations must contain both the expected session and its previous source session')
    if any(d > expected for d in incoming):
        raise ValueError('Observations exceed the requested completed data date')
    changes = []
    for day, row in incoming.items():
        before = old.get(day)
        fields = [field for field in FIELDS if before is None or number(before[field], field) != number(row[field], field)]
        if fields:
            changes.append({'date': day, 'changed_fields': fields,
                            'before': {k: before[k] for k in (*FIELDS, 'erp')} if before else None,
                            'after': {k: row[k] for k in (*FIELDS, 'erp')}})
        old[day] = row
    return [old[d] for d in sorted(old)], {'expected_date': expected, 'previous_date_rechecked': previous,
                                         'rechecked_dates': sorted(incoming), 'changes': changes}


def buffett_ratio(market_cap_cny, gdp_cny):
    cap, gdp = number(market_cap_cny, 'market_cap_cny'), number(gdp_cny, 'gdp_cny')
    if cap <= 0 or gdp <= 0:
        raise ValueError('Market capitalization and GDP must both be positive and in CNY')
    return {'ratio': str(cap/gdp), 'percent': str(cap/gdp*100)}
