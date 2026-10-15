"""Standalone ERP replay, free-source candidate and authorized official-curve run.

This command never edits the supplied workbook, silently mixes the Investing
benchmark close with a ChinaBond curve, or fills missing dates. Replay and
candidate modes are review artifacts. Free-official implements the user's
explicit request for all-free sources while retaining the definition audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .calculate import calculate_row, load_history, number
    from .fetch_free_pe import fetch_pe
    from .fetch_free_bond import fetch_bond, fetch_bond_history, METHOD_ID as BOND_METHOD_ID, CURVE_NAME
except ImportError:
    from calculate import calculate_row, load_history, number
    from fetch_free_pe import fetch_pe
    from fetch_free_bond import fetch_bond, fetch_bond_history, METHOD_ID as BOND_METHOD_ID, CURVE_NAME

CHINA = timezone(timedelta(hours=8))
HISTORY_START = '2015-01-01'
THRESHOLD = Decimal('0.06')
CSI_ENDPOINT = 'https://www.csindex.com.cn/csindex-home/perf/index-perf'
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ERPWorkflowError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def _day(value):
    try:
        result = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ERPWorkflowError('invalid_date', 'Use an explicit YYYY-MM-DD expected data date.') from None
    if result.isoformat() != value:
        raise ERPWorkflowError('invalid_date', 'Use an explicit YYYY-MM-DD expected data date.')
    return value


def _normalize_close(raw, wanted_dates):
    try:
        payload = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, UnicodeError):
        raise ERPWorkflowError('invalid_close_response', 'CSI did not return finite UTF-8 JSON.') from None
    if (not isinstance(payload, dict) or str(payload.get('code')) != '200'
            or payload.get('success') is False or not isinstance(payload.get('data'), list)):
        raise ERPWorkflowError('invalid_close_response', 'CSI returned an error or changed its schema.')
    result = {}
    for item in payload['data']:
        if not isinstance(item, dict) or item.get('indexCode') != '000300':
            raise ERPWorkflowError('invalid_close_response', 'CSI reply includes an unexpected index.')
        stamp = item.get('tradeDate')
        if not isinstance(stamp, str) or len(stamp) != 8 or not stamp.isdigit():
            raise ERPWorkflowError('invalid_close_response', 'CSI reply has an invalid date.')
        day = _day(f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}')
        if day in result:
            raise ERPWorkflowError('duplicate_date', 'CSI reply contains duplicate sessions.')
        close = number(item.get('close'), 'CSI close')
        if close <= 0:
            raise ERPWorkflowError('invalid_close_response', 'CSI close is not positive.')
        result[day] = {'date': day, 'close': str(close), 'source': 'CSI_official_index_performance'}
        # ``peg`` is deliberately ignored: it is not the requested PE-TTM source.
    missing = sorted(set(wanted_dates) - result.keys())
    if missing:
        raise ERPWorkflowError('missing_close_session', 'CSI lacks required sessions: ' + ', '.join(missing))
    return [result[d] for d in sorted(wanted_dates)]


def fetch_close(expected_date, previous_date, output_dir):
    wanted = {_day(expected_date), _day(previous_date)}
    params = {'indexCode': '000300', 'startDate': min(wanted).replace('-', ''),
              'endDate': max(wanted).replace('-', '')}
    url = CSI_ENDPOINT + '?' + urlencode(params)
    try:
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}), timeout=30) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise ERPWorkflowError('close_http_error', f'CSI returned HTTP {exc.code}.') from None
    except (URLError, TimeoutError, OSError):
        raise ERPWorkflowError('close_network_error', 'CSI close acquisition failed; no workbook fallback was used.') from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ERPWorkflowError('invalid_close_response', 'CSI response exceeds the size limit.')
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CHINA).strftime('%Y%m%dT%H%M%S%f')
    path = output / f'csindex-close-{stamp}.json'
    path.write_bytes(raw)
    return {'source': 'CSI_official_index_performance', 'rows': _normalize_close(raw, wanted),
            'raw_evidence': {'url': url, 'path': str(path.resolve()),
                             'sha256': hashlib.sha256(raw).hexdigest()},
            'captured_at': datetime.now(CHINA).isoformat()}


def _load_bond_rows(path, expected_date):
    path = Path(path)
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal)
    except (ValueError, UnicodeError):
        raise ERPWorkflowError('invalid_bond_history', 'Bond history must be UTF-8 normalized JSON.') from None
    records = payload if isinstance(payload, list) else payload.get('normalized') if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise ERPWorkflowError('invalid_bond_history', 'Bond history contains no normalized rows.')
    by_date = {}
    for row in records:
        if (not isinstance(row, dict) or row.get('method_id') != BOND_METHOD_ID
                or row.get('curve_name') != CURVE_NAME or row.get('tenor_years') != 10):
            raise ERPWorkflowError('incompatible_bond_history', 'All bond rows must identify the same official treasury ten-year curve.')
        day = _day(row.get('date'))
        if day in by_date:
            raise ERPWorkflowError('duplicate_date', 'Bond history has duplicate dates.')
        raw_yield = str(row.get('yield_source_text', row.get('yield_percent_number')))
        value = number(raw_yield, 'yield_percent_number')
        if value != number(row.get('yield_percent_number'), 'yield_percent_number') or not Decimal('-5') < value < Decimal('30'):
            raise ERPWorkflowError('invalid_bond_history', 'Bond value and published decimal text disagree or are out of range.')
        by_date[day] = {**row, 'date': day, 'yield_source_text': raw_yield,
                        'yield_percent_number': str(value)}
    manifest_path = path.with_name(path.name.replace('-normalized.json', '-manifest.json'))
    manifest = None
    if manifest_path != path and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        if manifest.get('status') != 'ok' or manifest.get('method_id') != BOND_METHOD_ID:
            raise ERPWorkflowError('invalid_bond_history', 'Bond capture manifest is not a completed official-source fetch.')
    evidence = {'normalized_path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest(),
                'manifest_path': str(manifest_path.resolve()) if manifest is not None else None,
                'raw_capture_manifest_available': manifest is not None,
                'source_record_count': len(by_date), 'source_start': min(by_date), 'source_end': max(by_date),
                'reused_declared_snapshot': True,
                'requested_expected_date': expected_date,
                'capture_manifest': manifest}
    return by_date, evidence


def _write_csv(path, rows):
    fields = ['date', 'close', 'pe_ttm_mcw', 'pe_ttm_mcw_raw', 'bond_yield_number',
              'erp', 'erp_percent', 'close_source', 'pe_source', 'bond_source']
    with Path(path).open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def rule_copy(rows, mode):
    latest, previous = rows[-1], rows[-2]
    value = Decimal(latest['erp_percent'])
    change = value - Decimal(previous['erp_percent'])
    movement = '上升' if change > 0 else '下降' if change < 0 else '持平'
    verb = '升至' if change > 0 else '降至' if change < 0 else '为'
    side = '高于' if Decimal(latest['erp']) > THRESHOLD else '低于' if Decimal(latest['erp']) < THRESHOLD else '等于'
    day = date.fromisoformat(latest['date'])
    title = f'【股债性价比】{day.month}月{day.day}日沪深300ERP{verb}{value:.2f}%，{side}6%参考线'
    delta = f'较前一交易日{movement}{abs(change):.2f}个百分点' if change else '与前一交易日持平'
    assessment = ('股债相对吸引力较前一交易日有所上升' if change > 0 else
                  '股债相对吸引力较前一交易日有所下降' if change < 0 else '股债相对吸引力较前一交易日变化不大')
    body = f'截至{day.year}年{day.month}月{day.day}日，沪深300股权风险溢价为{value:.2f}%，{delta}，{side}6%参考线。按本指标口径，{assessment}。'
    status = ('离线重放：数据来自用户上传工作簿，并非本次联网更新。' if mode == 'workbook-replay' else
              '免费源正式口径：国债使用中债国债收益率曲线，全历史已重算；PE最近两日来自雪球基金，历史保留原工作簿。' if mode == 'free-official' else
              '免费源候选：全历史国债收益率已换为中债国债收益率曲线并重算；PE最近两日来自雪球基金。此文件用于候选方案核对。')
    try:from .delivery import complete_body
    except ImportError:from delivery import complete_body
    return f"# {title}\n\n{complete_body('valuation',body)}\n\n{status}\n"


def _render(rows, output_dir, *, window_years=10):
    try:
        from .render_erp import render_erp
    except ImportError:
        from render_erp import render_erp
    return render_erp(rows, output_dir, threshold=float(THRESHOLD), window_years=window_years)


def _completed_run(output, expected, mode, input_hash, bond_input_hash, window_years):
    path = output / 'verification.json'
    try:
        metadata = json.loads(path.read_text(encoding='utf-8'))
        if (metadata.get('mode') != mode or metadata.get('expected_date') != expected
                or metadata.get('source_sha256') != input_hash
                or metadata.get('input_bond_history_sha256') != bond_input_hash
                or metadata.get('window_years') != window_years
                or metadata.get('status') not in ('replay_complete', 'candidate_complete', 'official_complete')):
            return None
        if not metadata.get('artifact_sha256'):
            return None
        for name, digest in metadata['artifact_sha256'].items():
            artifact = output / name
            if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                return None
        return {**metadata, 'completed_date_cache_reused': True, 'network_requests_this_call': 0}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def run_erp(workbook, out_dir, *, mode, expected_date, bond_history=None, force_refresh=False, window_years=10):
    expected = _day(expected_date)
    if isinstance(window_years, bool) or not isinstance(window_years, int) or window_years < 1:
        raise ERPWorkflowError('invalid_window', 'Chart window years must be a positive integer.')
    if mode not in ('workbook-replay', 'free-candidate', 'free-official'):
        raise ERPWorkflowError('invalid_mode', 'Use workbook-replay, free-candidate or free-official.')
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    input_path = Path(workbook).resolve()
    if input_path == (output / '数据快照.json').resolve():
        raise ERPWorkflowError('input_output_collision', 'Input history cannot be overwritten by its output.')
    input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    bond_input_hash = hashlib.sha256(Path(bond_history).read_bytes()).hexdigest() if bond_history else None
    if not force_refresh:
        cached = _completed_run(output, expected, mode, input_hash, bond_input_hash, window_years)
        if cached is not None:
            return cached
    seed = [r for r in load_history(input_path) if HISTORY_START <= r['date'] <= expected]
    if len(seed) < 2:
        raise ERPWorkflowError('insufficient_history', 'At least two uploaded sessions since 2015 are required.')
    by_date = {r['date']: r for r in seed}
    seed_by_date = dict(by_date)
    previous = max(d for d in by_date if d < expected)
    metadata = {'mode': mode, 'release_ready': False,
                'artifact_kind': 'workbook_replay' if mode == 'workbook-replay' else 'official_free_source_run' if mode == 'free-official' else 'free_source_candidate',
                'expected_date': expected, 'previous_session': previous,
                'source_workbook_or_history': str(input_path), 'source_sha256': input_hash,
                'input_bond_history_sha256': bond_input_hash,
                'window_years': window_years,
                'window_policy': 'rolling_calendar_years_ending_on_expected_data_date; full_source_history_preserved',
                'completed_date_cache_reused': False,
                'source_file_modified': False, 'captured_at': datetime.now(CHINA).isoformat(),
                'threshold': str(THRESHOLD), 'threshold_basis': 'PLUS template fixed 6%; F53 STDEVA is not a threshold',
                'no_AI_required': True, 'API_calls_for_text': 0,
                'publication_status': 'review_only_not_deployed',
                'coverage_basis': 'all uploaded index/PE session dates since 2015 plus explicitly fetched latest session',
                'independent_full_exchange_calendar_verified': False}
    original_bond_definition = (BOND_METHOD_ID if all(r.get('bond_source') == BOND_METHOD_ID for r in seed) else
                                'Investing China ten-year benchmark closing yield as specified in the uploaded workbook')
    source_report = {'mode': mode, 'release_ready': False, 'source_change_requires_confirmation': False,
                     'original_bond_definition': original_bond_definition,
                     'source_transitions': [], 'historical_differences': [],
                     'history_silently_spliced': False, 'date_fill_applied': False}
    if mode == 'free-official':
        source_report['source_policy'] = {
            'user_requested_all_free': True, 'approved_bond_method': BOND_METHOD_ID,
            'paid_source_requests': 0,
            'historical_pe_policy': 'retain_user_uploaded_PE_history_with_explicit_Danjuan_transition_for_latest_two_sessions',
            'bond_definition_difference_disclosed': True,
            'approval_basis': 'User explicitly requested all data sources to be free; official ChinaBond curve selected within that request.'}
    gaps = {'expected_date': expected, 'previous_session': previous,
            'missing_bond_dates': [], 'missing_close_dates': [], 'missing_pe_dates': [],
            'no_forward_fill': True, 'all_required_dates_present': False}
    rows = []
    if mode == 'workbook-replay':
        if expected not in by_date:
            gaps['missing_close_dates'] = [expected]
            gaps['missing_pe_dates'] = [expected]
        else:
            rows = [{**r, 'close_source': 'uploaded_workbook', 'pe_source': 'uploaded_workbook',
                     'bond_source': 'uploaded_workbook_original_series'} for r in seed]
            gaps['all_required_dates_present'] = True
        source_report['online_data_acquisition_performed'] = False
        source_report['note'] = 'Faithful calculation replay only; no claim that uploaded figures are current online observations.'
    else:
        # No old workbook close or PE can serve as a live acquisition fallback.
        close = fetch_close(expected, previous, output / 'raw' / 'close')
        pe = fetch_pe(expected, previous, output / 'raw' / 'pe', use_completed_cache=not force_refresh)
        close_rows = {r['date']: r for r in close['rows']}
        pe_rows = {r['date']: r for r in pe['rows']}
        gaps['missing_close_dates'] = sorted({expected, previous} - close_rows.keys())
        gaps['missing_pe_dates'] = sorted({expected, previous} - pe_rows.keys())
        if gaps['missing_close_dates'] or gaps['missing_pe_dates']:
            raise ERPWorkflowError('missing_latest_observation', 'Live PE and close must each include both required sessions.')
        transitions = []
        for day in (previous, expected):
            original = by_date.get(day)
            pe_raw = number(pe_rows[day]['pe_ttm_mcw_raw'], 'pe_ttm_mcw_raw')
            pe_input = pe_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            by_date[day] = {**(original or {}), 'date': day, 'close': close_rows[day]['close'],
                            'pe_ttm_mcw': str(pe_input), 'pe_ttm_mcw_raw': str(pe_raw),
                            'pe_source': pe_rows[day].get('source', pe['source']),
                            'close_source': close['source'],
                            'source_status': 'free_sources_verified_latest_two_sessions',
                            'pe_input_precision_policy': 'two_decimal_places_matching_latest_uploaded_input_convention'}
            transitions.append({'date': day, 'old_close': original['close'] if original else None,
                                'new_close': close_rows[day]['close'], 'old_pe': original['pe_ttm_mcw'] if original else None,
                                'new_pe_input': str(pe_input), 'new_pe_raw': str(pe_raw),
                                'new_pe_source': pe_rows[day].get('source', pe['source'])})
        if bond_history is None:
            capture = fetch_bond_history(HISTORY_START, expected, output / 'raw' / 'bond')
            bond_history = capture['normalized_path']
        bonds, bond_evidence = _load_bond_rows(bond_history, expected)
        if mode == 'free-official' and max(bonds) < expected:
            # Routine update: one batch containing the latest and previous source
            # session. An older history gap remains a gap, never a filled value.
            recent = fetch_bond([previous, expected], output / 'raw' / 'bond-update')
            updated = {r['date']: r for r in recent['normalized']}
            bonds.update(updated)
            rolling_path = output / 'raw' / 'bond' / 'official-rolling-normalized.json'
            _write_json(rolling_path, [bonds[d] for d in sorted(bonds)])
            bonds, updated_evidence = _load_bond_rows(rolling_path, expected)
            bond_evidence = {'initial_history': bond_evidence, 'latest_two_session_update': recent,
                             'combined_history': updated_evidence, 'same_official_method_only': True}
        required = sorted(by_date)
        gaps['missing_bond_dates'] = [day for day in required if day not in bonds]
        source_report.update({'online_data_acquisition_performed': True,
                              'close_acquisition': close, 'pe_acquisition': pe, 'bond_acquisition': bond_evidence,
                              'candidate_bond_definition': BOND_METHOD_ID,
                              'is_original_benchmark_close': False,
                              'bond_history_recalculated_in_full': not gaps['missing_bond_dates'],
                              'source_transitions': transitions,
                              'pe_history_equivalence': pe.get('lixinger_history_equivalence', 'not_asserted'),
                              'pe_precision_policy': 'original history unchanged; new two-session raw values retained and rounded to 2dp before ERP calculation',
                              'bond_precision_policy': 'all published decimal digits retained; percentage-number divided by 100 exactly once'})
        for day in required:
            if day not in bonds:
                continue
            original = seed_by_date.get(day)
            row = {**by_date[day], 'bond_yield_number': bonds[day]['yield_source_text'],
                    'close_source': by_date[day].get('close_source', 'uploaded_workbook'),
                    'pe_source': by_date[day].get('pe_source', 'uploaded_workbook_original_PE_history'),
                    'bond_source': BOND_METHOD_ID}
            if day not in (previous, expected):
                row['source_status'] = 'uploaded_PE_and_close_recomputed_with_official_treasury_curve'
            calculated = calculate_row(row)
            rows.append(calculated)
            if original is not None:
                source_report['historical_differences'].append({
                    'date': day, 'original_bond': original['bond_yield_number'],
                    'candidate_bond': calculated['bond_yield_number'],
                    'original_erp': original['erp'], 'candidate_erp': calculated['erp'],
                    'erp_change_percentage_points': str(Decimal(calculated['erp_percent']) - Decimal(original['erp_percent']))})
        gaps['all_required_dates_present'] = not any(gaps[k] for k in ('missing_bond_dates', 'missing_close_dates', 'missing_pe_dates'))
        gaps['required_index_pe_sessions'] = len(required)
        gaps['extra_bond_only_sessions_excluded'] = sum(day not in by_date for day in bonds if HISTORY_START <= day <= expected)
    rows = sorted(rows, key=lambda r: r['date'])
    metadata.update({'actual_start': rows[0]['date'] if rows else None,
                     'actual_end': rows[-1]['date'] if rows else None, 'points': len(rows),
                     'expected_date_present': any(r['date'] == expected for r in rows),
                     'all_required_dates_present': gaps['all_required_dates_present']})
    source_report['historical_difference_count'] = sum(
        Decimal(r['erp_change_percentage_points']) != 0 for r in source_report['historical_differences'])
    _write_json(output / '来源兼容性报告.json', source_report)
    _write_json(output / '数据缺口报告.json', gaps)
    _write_json(output / '数据快照.json', rows)
    _write_csv(output / '绘图数据.csv', rows)
    if not gaps['all_required_dates_present']:
        metadata.update({'status': 'incomplete_data', 'image_generated': False,
                         'reason': 'Required dates are missing; partial observations were saved but no chart or publishable text was generated.'})
        _write_json(output / 'verification.json', metadata)
        return metadata
    # Rule text is produced without any model call; AI enhancement can run later.
    (output / '快讯文案_规则.md').write_text(rule_copy(rows, mode), encoding='utf-8')
    try:
        image_path, chart_audit = _render(rows, output, window_years=window_years)
    except (ValueError, AssertionError) as exc:
        metadata.update({'status': 'render_blocked', 'image_generated': False, 'reason': str(exc)})
        _write_json(output / 'verification.json', metadata)
        return metadata
    metadata.update({'status': 'replay_complete' if mode == 'workbook-replay' else 'official_complete' if mode == 'free-official' else 'candidate_complete',
                     'release_ready': mode == 'free-official',
                     'image_generated': True, 'image_path': str(Path(image_path).resolve()), 'chart_audit': chart_audit})
    source_report['release_ready'] = metadata['release_ready']
    _write_json(output / '来源兼容性报告.json', source_report)
    if hashlib.sha256(input_path.read_bytes()).hexdigest() != input_hash:
        raise ERPWorkflowError('source_modified', 'Input workbook changed during execution.')
    artifacts = [output / '来源兼容性报告.json', output / '数据缺口报告.json', output / '数据快照.json',
                 output / '绘图数据.csv', output / '快讯文案_规则.md', Path(image_path)]
    try:from .delivery import write_delivery,split_markdown
    except ImportError:from delivery import write_delivery,split_markdown
    write_delivery(output,category='valuation_erp',data_date=expected,
                   rule=split_markdown(rule_copy(rows,mode)),images=[Path(image_path).resolve()],image_required=True,
                   ready=metadata['release_ready'],limitations=['本稿仅为ERP部分，尚未合并巴菲特指数栏目'])
    artifacts += [output/name for name in ('标题.txt','正文.txt','发布稿.md','交付清单.json')]
    metadata['artifact_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}
    _write_json(output / 'verification.json', metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', required=True, help='Uploaded read-only XLSX or imported normalized history JSON.')
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--mode', choices=['workbook-replay', 'free-candidate', 'free-official'], required=True)
    parser.add_argument('--expected-date', required=True, help='Explicit completed data date, YYYY-MM-DD.')
    parser.add_argument('--window-years', type=int, default=10, help='Rolling calendar-year chart window (default: 10). Full source history is preserved.')
    parser.add_argument('--bond-history', help='Reuse a declared ChinaBond normalized JSON snapshot; no cached fallback.')
    parser.add_argument('--force-refresh', action='store_true', help='Explicitly bypass a completed exact-date run and PE capture.')
    args = parser.parse_args()
    try:
        result = run_erp(args.workbook, args.out_dir, mode=args.mode,
                         expected_date=args.expected_date, bond_history=args.bond_history,
                         force_refresh=args.force_refresh, window_years=args.window_years)
    except Exception as exc:
        result = {'status': 'failed', 'release_ready': False, 'mode': args.mode,
                  'expected_date': args.expected_date, 'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)}
        _write_json(Path(args.out_dir) / 'verification.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result['status'] in ('incomplete_data', 'render_blocked'):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
