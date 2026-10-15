"""Free-source date and unit checks against the supplied 2026-10-14 report."""
import json
from pathlib import Path

import pytest

from app.briefs.engines.oil_etf.previous import PreviousSourceError, parse
from app.briefs import sources


def _page(day, name, symbol, contracts):
    return (f'<div>Etf holdings as of <span class="background-ya">{day}</span></div>'
            f'<table><tr><td>98%</td><td>{name} Dec26</td><td>{symbol}</td>'
            f'<td></td><td>{contracts}</td></tr></table>').encode()


def test_secondary_holdings_require_matching_date_and_contract():
    page = _page('October 13, 2026', 'WTI CRUDE FUTURE', 'CLZ6', '16325')
    assert parse('USO', page, '2026-10-13')['futures_barrels'] == '16325000'
    with pytest.raises(PreviousSourceError, match='需要2026-10-12'):
        parse('USO', page, '2026-10-12')
    with pytest.raises(PreviousSourceError, match='代码异常'):
        parse('USO', page.replace(b'CLZ6', b'COZ6'), '2026-10-13')


def test_oil_report_matches_screenshot_futures_changes(tmp_path, monkeypatch):
    sample = Path(__file__).parents[1] / 'app/briefs/samples/oil_etf/result.json'
    official = json.loads(sample.read_text(encoding='utf-8'))['facts']['official_source']
    monkeypatch.setattr('app.briefs.engines.oil_etf.run.fetch', lambda out, day: official)
    prior = {'data_date': '2026-10-13', 'provider': 'CompaniesMarketCap public holdings',
             'funds': {'USO': {'futures_barrels': '16325000', 'source_url': 'https://example.test/uso', 'raw_sha256': 'a'},
                       'BNO': {'futures_barrels': '6962000', 'source_url': 'https://example.test/bno', 'raw_sha256': 'b'}}}
    monkeypatch.setattr('app.briefs.engines.oil_etf.previous.fetch', lambda day, archive: prior)
    out = tmp_path / 'run'
    out.mkdir()
    state = tmp_path / 'state'
    state.mkdir()
    result = sources.oil_etf('2026-10-14', out, state)
    uso, bno = result['facts']['funds']
    assert uso['change_barrels'] == '-489000.0000'
    assert bno['change_barrels'] == '1000.0000'
    assert '减少48.90万桶' in result['dynamic_body']
    assert '增加0.10万桶' in result['dynamic_body']
    assert '期货持仓较' in result['dynamic_body']
    assert 'oil-previous-source.json' in [p.name for p in out.iterdir()]
