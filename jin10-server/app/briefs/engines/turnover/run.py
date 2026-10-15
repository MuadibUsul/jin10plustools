"""Standalone turnover job. No Codex, MCP or AI is needed by the rule track."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from delivery import write_delivery,complete_body

HERE = Path(__file__).resolve().parent
CHINA = timezone(timedelta(hours=8))


class JobError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def job_lock(path):
    """An OS lock is released on process exit, including a crashed process."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open('a+b')
    try:
        if not path.stat().st_size:
            file.write(b'0')
            file.flush()
        file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise JobError('busy', 'Another turnover job is running') from exc
        yield
    finally:
        file.close()


def expected_session(now, cutoff='16:00', calendar_path=None):
    """A supplied official calendar is authoritative; weekdays are only a fallback.

    Without an exchange calendar a public holiday becomes pending_data, never
    a successful new issue based on old data. No holiday dates are guessed.
    """
    now = now.astimezone(CHINA)
    ready_time = time.fromisoformat(cutoff)
    target = now.date() if now.time().replace(tzinfo=None) >= ready_time else now.date()-timedelta(days=1)
    if calendar_path:
        try:
            calendar = json.loads(Path(calendar_path).read_text(encoding='utf-8'))
            begin, end = date.fromisoformat(calendar['valid_from']), date.fromisoformat(calendar['valid_to'])
            if begin > end or not isinstance(calendar['sessions'], list):
                raise ValueError('Invalid calendar format')
            sessions = sorted({date.fromisoformat(d) for d in calendar['sessions']})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise JobError('calendar_invalid', 'Trading calendar is missing or invalid') from exc
        if not begin <= target <= end:
            raise JobError('calendar_expired', 'Trading calendar does not cover the required date')
        choices = [d for d in sessions if d <= target]
        if not choices:
            raise JobError('calendar_invalid', 'Trading calendar has no previous session')
        return choices[-1].isoformat()
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target.isoformat()


def select_current(rows, expected):
    date.fromisoformat(expected)
    available = [r for r in rows if r['date'] <= expected]
    if len(available) < 2 or available[-1]['date'] != expected:
        latest = available[-1]['date'] if available else 'none'
        raise JobError('pending_data', f'Expected {expected}; latest available completed record is {latest}')
    return available[-1], available[-2]


def revision_key(data, config, expected):
    digest = hashlib.sha256()
    digest.update(data.read_bytes())
    digest.update(json.dumps({'start': config['start'], 'expected': expected}, sort_keys=True).encode())
    for path in [HERE/'build_turnover.py', HERE/'render_template.py', HERE/'copy_tracks.py', HERE/'delivery.py', *sorted((HERE/'assets').glob('*'))]:
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def write_copy(path, copy):
    path.write_text(f"# {copy['title']}\n\n{complete_body('turnover',copy['body'])}\n\n"
                    '[数据来源：乐咕乐股](https://legulegu.com/stockdata/market-turn-over-ratio-statistics)\n',
                    encoding='utf-8')


def publish_latest(root, expected, out, manifest):
    """A manual backfill is archived without moving the live entry backwards."""
    target = root/'latest.json'
    if target.exists():
        previous = json.loads(target.read_text(encoding='utf-8'))
        if previous['data_date'] > expected:
            return
    write_json(target, {'data_date': expected, 'output_dir': str(out), 'manifest': manifest})


def complete_ai(out, manifest, *, enabled=True):
    from copy_tracks import generate_deepseek_copy
    facts = json.loads((out/'facts.json').read_text(encoding='utf-8'))
    ai = generate_deepseek_copy(facts) if enabled else {'status': 'disabled'}
    write_json(out/'deepseek_status.json', ai)
    manifest['deepseek'] = {'status': ai['status']}
    if ai['status'] == 'ok':
        write_copy(out/'DeepSeek版文案.md', ai)
        manifest['deepseek']['file'] = 'DeepSeek版文案.md'
    manifest['phase'] = 'complete'
    write_delivery(out,category='turnover',data_date=manifest['data_date'],
                   rule=json.loads((out/'rules.json').read_text(encoding='utf-8')),
                   images=[out/manifest['chart']],image_required=True,deepseek=ai)
    manifest['updated_at'] = datetime.now(CHINA).isoformat()
    write_json(out/'manifest.json', manifest)
    return manifest


def run_job(args, config, root):
    from build_turnover import build_outputs, load_and_calculate
    from copy_tracks import build_facts, generate_rule_copy
    now = datetime.now(CHINA)
    mode = 'replay' if args.input else 'live'
    if args.input and not args.expected_date:
        raise JobError('configuration_error', 'Historical input requires --expected-date; replay cannot update live latest.json')
    calendar_path = config.get('calendar_file')
    if calendar_path and not Path(calendar_path).is_absolute():
        calendar_path = args.config.resolve().parent/calendar_path
    expected = args.expected_date or expected_session(now, config['data_ready_after'], calendar_path)
    date.fromisoformat(expected)
    config=dict(config)
    years=config.get('window_years')
    if years is not None:
        if not isinstance(years,int) or isinstance(years,bool) or not 1<=years<=30:
            raise JobError('configuration_error','window_years must be an integer from 1 to 30')
        endpoint=date.fromisoformat(expected)
        try:start=endpoint.replace(year=endpoint.year-years)
        except ValueError:start=endpoint.replace(year=endpoint.year-years,day=28)
        config['start']=start.isoformat()
    if expected > now.date().isoformat():
        raise JobError('configuration_error', 'A future expected data date is not allowed')
    run_id = now.strftime('%Y%m%dT%H%M%S%f') + '-' + uuid4().hex[:8]
    run_dir = root/'runs'/run_id
    run_dir.mkdir(parents=True)
    if args.input:
        source_path = args.input.resolve()
        metadata = {'source': 'explicit historical replay input', 'mode': 'replay',
                    'fetched_at': None, 'imported_at': now.isoformat()}
    else:
        from fetch_data import FetchError, fetch_snapshot
        try:
            metadata = fetch_snapshot(run_dir/'source', timeout_seconds=config['fetch_timeout_seconds'])
        except FetchError as exc:
            raise JobError('source_' + exc.code, str(exc)) from exc
        source_path = Path(metadata['path'])
    rows = load_and_calculate(source_path)
    current, previous = select_current(rows, expected)
    key = revision_key(source_path, config, expected)
    out = root/('replays' if mode == 'replay' else 'outputs')/f'{expected}-{key}'
    manifest_path = out/'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        required = [out/'facts.json', out/'规则版文案.md', out/manifest['chart']]
        if manifest.get('rules', {}).get('status') == 'ok' and all(p.exists() for p in required):
            if args.retry_ai or manifest.get('phase') != 'complete':
                manifest = complete_ai(out, manifest, enabled=not args.no_ai)
            else:
                ai_path=out/'deepseek_status.json'
                ai=json.loads(ai_path.read_text(encoding='utf-8')) if ai_path.exists() else {'status':'not_configured'}
                write_delivery(out,category='turnover',data_date=expected,
                    rule=json.loads((out/'rules.json').read_text(encoding='utf-8')),
                    images=[out/manifest['chart']],image_required=True,deepseek=ai)
            manifest['reused'] = True
            if mode == 'live':
                publish_latest(root, expected, out, manifest)
            return manifest
    # Revisions are retained. A partial directory never becomes live latest.json.
    out.mkdir(parents=True, exist_ok=True)
    snapshot = out/'source.json'
    shutil.copyfile(source_path, snapshot)
    write_json(out/'source_metadata.json', metadata)
    reference = args.reference.resolve() if args.reference else None
    result = build_outputs(snapshot, out, reference=reference, start=config['start'], as_of=expected)
    facts = build_facts(current, previous)
    rule = generate_rule_copy(facts)
    write_json(out/'facts.json', facts)
    write_json(out/'rules.json', rule)
    write_copy(out/'规则版文案.md', rule)
    manifest = {'mode': mode, 'data_date': expected, 'revision': key,
                'created_at': now.isoformat(), 'output_dir': str(out),
                'phase': 'rules_ready', 'chart': Path(result['png']).name,
                'rules': {'status': 'ok', 'file': '规则版文案.md'},
                'deepseek': {'status': 'pending'}}
    write_json(manifest_path, manifest)
    # Rules are available before any model request; API latency cannot erase them.
    write_delivery(out,category='turnover',data_date=expected,rule=rule,
                   images=[out/manifest['chart']],image_required=True,deepseek={'status':'pending'})
    if mode == 'live':
        publish_latest(root, expected, out, manifest)
    print(json.dumps({'event': 'rules_ready', 'data_date': expected, 'output_dir': str(out)}, ensure_ascii=False), flush=True)
    manifest = complete_ai(out, manifest, enabled=not args.no_ai)
    if mode == 'live':
        publish_latest(root, expected, out, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=HERE/'config.json')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--input', type=Path, help='Historical replay only; never changes live latest.json')
    parser.add_argument('--reference', type=Path, help='Optional source chart series for full pointwise verification')
    parser.add_argument('--expected-date', help='Explicit required data date, YYYY-MM-DD')
    parser.add_argument('--no-ai', action='store_true')
    parser.add_argument('--retry-ai', action='store_true', help='Retry the API track for an existing revision (can incur a fee)')
    args = parser.parse_args(argv)
    root = args.data_dir or Path(os.getenv('TURNOVER_DATA_DIR', str(HERE/'data')))
    root = root.resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with job_lock(root/'job.lock'):
            config = {'start': '2015-01-01', 'window_years':10, 'data_ready_after': '16:00', 'fetch_timeout_seconds': 90}
            try:
                if args.config.exists():
                    loaded = json.loads(args.config.read_text(encoding='utf-8'))
                    if not isinstance(loaded, dict):
                        raise ValueError('Config must be a JSON object')
                    config.update(loaded)
                date.fromisoformat(config['start'])
                time.fromisoformat(config['data_ready_after'])
                if not 10 <= config['fetch_timeout_seconds'] <= 180:
                    raise ValueError('Fetch timeout must be between 10 and 180 seconds')
                if config.get('calendar_file') is not None and not isinstance(config['calendar_file'], str):
                    raise ValueError('Calendar path must be a string or null')
            except (ValueError, TypeError, OSError, KeyError) as exc:
                raise JobError('configuration_error', 'Invalid workflow configuration file') from exc
            result = run_job(args, config, root)
            status = {'status': 'ok', 'checked_at': datetime.now(CHINA).isoformat(), 'result': result}
            write_json(root/'status.json', status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    except JobError as exc:
        status = {'status': exc.status, 'message': str(exc), 'checked_at': datetime.now(CHINA).isoformat()}
        # A duplicate process must not overwrite the active worker's status.
        if exc.status != 'busy':
            write_json(root/'status.json', status)
        print(json.dumps(status, ensure_ascii=False), file=sys.stderr)
        return 75 if exc.status == 'busy' else 3
    except Exception as exc:
        # No URLs with temporary tokens, provider response bodies, or secrets in logs.
        status = {'status': 'failed', 'error_type': type(exc).__name__, 'checked_at': datetime.now(CHINA).isoformat()}
        try:
            write_json(root/'status.json', status)
        except OSError:
            pass
        print(json.dumps(status, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
