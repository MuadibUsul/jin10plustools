"""Real response regressions for the missing close and contaminated projection."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.briefs import sources, volume_market
from app.briefs.data_reliability import extract, save_points
from app.briefs.failures import SourceContractError, MissingObservations
from app.briefs.observation_quality import quarantine_volume, VOLUME_METHOD
from app.briefs.repair import restore_working_history, recover_snapshots
from app.db.models import BriefDataPoint, BriefSourceSnapshot

FIXTURES = Path(__file__).parent / 'fixtures' / 'volume-market'


def captured(monkeypatch):
    primary = (FIXTURES / 'jin10-20261124.json').read_bytes()
    @contextmanager
    def reply(*args, **kwargs):
        class Response:
            def read(self, *args):return primary
        yield Response()
    monkeypatch.setattr(sources, 'urlopen', reply)
    monkeypatch.setattr(sources, 'completed_session', lambda _: date(2026, 11, 24))
    def read_market(url, folder, name):
        market = 'sh' if '1.000001' in url else 'sz'
        raw = (FIXTURES / f'eastmoney-{market}-20261124.json').read_bytes()
        (folder / name).write_bytes(raw)
        return raw
    monkeypatch.setattr(volume_market, 'read_market', read_market)
    monkeypatch.setenv('DATA_COLLECTION_ONLY', '1')


def test_real_missing_and_intraday_close_use_numeric_market_observations(tmp_path, monkeypatch):
    captured(monkeypatch)
    state = tmp_path / 'state';state.mkdir()
    out = tmp_path / 'run';out.mkdir()
    result = sources.volume('2026-11-23', out, state, refresh=True)
    assert result['facts']['amount_yi'] == '15528.8017283684'
    assert result['facts']['previous_date'] == '2026-11-20'
    assert result['facts']['change_yi'] == '1306.80'
    assert result['facts']['change_pct'] == '+9.19'
    rows = sources.read(state / 'volume-history.json')['rows']
    day20 = next(row for row in rows if row['date'] == '2026-11-20')
    assert day20['source_kind'] == 'eastmoney_daily_index'
    assert Decimal(day20['turn']) == Decimal('14222.0042293739')
    assert Decimal(day20['vol']) == Decimal('1071.520334')
    comparison = sources.read(out / 'volume-market-2026-11-comparison.json')
    assert len(comparison['overlap_dates']) == 15 and not comparison['conflicting_dates']
    assert sources.read(out / 'volume-intraday-rejected.json')['dates'] == ['2026-11-23']
    assert set(point['method'] for point in extract('volume', out)) == {
        'jin10_hushen_turnover_yi_v1', 'eastmoney_hushen_index_turnover_yi_v1'}


def test_additive_history_does_not_resurrect_rejected_flash_or_intraday(tmp_path, monkeypatch):
    captured(monkeypatch)
    state = tmp_path / 'state';state.mkdir()
    out = tmp_path / 'run';out.mkdir()
    sources.save(state / 'volume-history.json', {'method': 'jin10_hushen_turnover_yi_v1', 'rows': [
        {'date': '2026-11-20', 'turn': '14222.01', 'vol': '1072',
         'source_kind': 'jin10_published_closing_flash'},
        {'date': '2026-11-23', 'turn': '9163.71158132', 'vol': '671.614390'}]})
    sources.volume('2026-11-23', out, state, refresh=True)
    rows = sources.read(state / 'volume-history.json')['rows']
    assert sources.read(state / 'volume-history.json')['method'] == VOLUME_METHOD
    assert not any(row.get('source_kind') == 'jin10_published_closing_flash' for row in rows)
    assert not any(row['date'] == '2026-11-23' and row['turn'] == '9163.71158132' for row in rows)


def test_independent_source_conflict_is_not_silently_merged(tmp_path, monkeypatch):
    captured(monkeypatch)
    original = volume_market.read_market
    def changed(url, folder, name):
        payload = json.loads(original(url, folder, name))
        payload['data']['klines'][0] = payload['data']['klines'][0].replace('847459066880.80', '847459066980.80')
        return json.dumps(payload).encode()
    monkeypatch.setattr(volume_market, 'read_market', changed)
    state = tmp_path / 'state';state.mkdir()
    out = tmp_path / 'run';out.mkdir()
    with pytest.raises(SourceContractError, match='不一致'):
        sources.volume('2026-11-23', out, state, refresh=True)
    assert not any(row['date'] == '2026-11-20' for row in sources.read(state / 'volume-history.json')['rows'])


@pytest.mark.parametrize('mutation', ['wrong_index', 'duplicate_date', 'future_date', 'bad_amount'])
def test_market_contract_rejects_scope_dates_and_invalid_values(mutation):
    payload = json.loads((FIXTURES / 'eastmoney-sz-20261124.json').read_bytes())
    if mutation == 'wrong_index':payload['data']['code'] = '399106'
    elif mutation == 'duplicate_date':payload['data']['klines'].append(payload['data']['klines'][0])
    elif mutation == 'future_date':payload['data']['klines'][0] = payload['data']['klines'][0].replace('2026-11-02', '2026-11-25')
    else:
        fields = payload['data']['klines'][0].split(',');fields[6] = 'NaN'
        payload['data']['klines'][0] = ','.join(fields)
    with pytest.raises(SourceContractError):
        volume_market.parse_market(json.dumps(payload).encode(), 'sz', '2026-11-01', '2026-11-24')


def test_database_quarantine_preserves_values_and_rebuilds_corrupt_projection(db_factory, seed, tmp_path):
    from app.briefs.service import enqueue
    team = seed['editor'].team_id
    raw = (FIXTURES / 'jin10-20261124.json').read_bytes()
    with db_factory() as db:
        job = enqueue(db, team, date(2026, 11, 24), 'volume')
        save_points(db, team, 'volume', [
            {'series': 'volume', 'method': 'jin10_hushen_turnover_yi_v1', 'date': '2026-11-23',
             'values': {'date': '2026-11-23', 'turn': '9163.71158132', 'vol': '671.614390'}},
            {'series': 'volume', 'method': 'jin10_hushen_turnover_yi_v1', 'date': '2026-11-20',
             'values': {'date': '2026-11-20', 'turn': '14222.01', 'vol': '1072',
                        'source_kind': 'jin10_published_closing_flash'}}], job.id)
        db.add(BriefSourceSnapshot(team_id=team, job_id=job.id, category='volume', attempt=1,
            data_date=date(2026,11,23),
            relative_path='volume-raw-page-1.json', sha256=hashlib.sha256(raw).hexdigest(),
            content=raw, byte_count=len(raw)))
        db.flush()
        rejected = quarantine_volume(db, team)
        assert len(rejected) == 1  # The flash was already rejected during ingestion.
        assert len(list(db.scalars(select(BriefDataPoint)))) == 2
        assert all(row.quality_status == 'rejected' for row in db.scalars(select(BriefDataPoint)))
        # Add a genuine independently sourced closing revision of the same date.
        save_points(db, team, 'volume', [{'series': 'volume', 'method': volume_market.SOURCE,
            'date': '2026-11-23', 'values': {'date': '2026-11-23', 'turn': '15528.8017283684',
            'vol': '1119.564804', 'source_kind': 'eastmoney_daily_index'}}], job.id)
        (tmp_path / 'volume-history.json').write_text('{truncated')
        restore_working_history(db, team, 'volume', tmp_path)
        rows = sources.read(tmp_path / 'volume-history.json')['rows']
        assert len(rows) == 1 and rows[0]['turn'] == '15528.8017283684'
        assert (tmp_path / 'volume-history-recovery-diagnostic.json').is_file()
        assert quarantine_volume(db, team) == []


def test_generation_reuses_verified_projection_without_network(tmp_path, monkeypatch):
    captured(monkeypatch)
    state = tmp_path / 'state';state.mkdir()
    out = tmp_path / 'capture';out.mkdir()
    sources.volume('2026-11-23', out, state, refresh=True)
    monkeypatch.delenv('DATA_COLLECTION_ONLY')
    monkeypatch.setattr(sources, 'urlopen', lambda *a, **k: pytest.fail('generation fetched network'))
    from app.briefs import charts
    monkeypatch.setattr(charts, 'volume_chart', lambda rows, folder: folder / 'chart.png')
    run = tmp_path / 'generate';run.mkdir()
    result = sources.volume('2026-11-23', run, state)
    assert result['facts']['acquisition'] == 'verified_observations_reused'
    assert result['facts']['supplemental_sources']


def test_retained_flash_draft_cannot_be_confirmed_even_without_legacy_digest(db_factory, seed, tmp_path):
    from app.briefs.observation_quality import volume_edition_exclusion
    with db_factory() as db:
        reason=volume_edition_exclusion(db,seed['editor'].team_id,{
            'facts':{'source':'https://flash.jin10.com/detail/20261120150127681800'}},tmp_path)
        assert '已隔离' in reason
