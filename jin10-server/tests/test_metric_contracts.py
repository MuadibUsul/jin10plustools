from decimal import Decimal
import pytest
from app.briefs.metric_contracts import Quantity,market_cap_to_gdp,production_contract,require_production_contract
from app.briefs.failures import SourceContractError


@pytest.mark.parametrize('unit,meaning',[('index_points','index_level'),('USD_billion','market_capitalization'),
                                       ('CNY_100_million','revenue')])
def test_sample_agreement_cannot_authorize_incompatible_dimensions(unit,meaning):
    with pytest.raises(SourceContractError):
        market_cap_to_gdp(Quantity('1200000',unit,meaning),Quantity('1401879','CNY_100_million','nominal_gdp'))


def test_compatible_currency_quantities_preserve_decimal_precision():
    ratio=market_cap_to_gdp(Quantity('1200000.12345678','CNY_100_million','market_capitalization'),
                           Quantity('1401879','CNY_100_million','nominal_gdp'))
    assert ratio==Decimal('1200000.12345678')/Decimal('1401879')


def test_current_wilshire_method_fails_even_if_old_payload_asserts_valid():
    payload={'valid':True,'facts':{'buffett_basis':'verified_market_cap'}}
    report=production_contract('us_barometer',payload)
    assert report['status']=='unverified' and report['inputs']['numerator']['unit']=='index_points'
    with pytest.raises(SourceContractError):require_production_contract('us_barometer',payload)
    assert require_production_contract('valuation',{})['status']=='validated_by_adapter'


def test_saved_legacy_version_is_visibly_blocked_without_rewriting_edition(db_factory,seed):
    from datetime import date
    from app.briefs.service import ensure_issue,append_version,version_out
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,11,24),'us_barometer')
        version=append_version(db,issue,{'title':'历史标题','dynamic_body':'历史正文',
            'data_date':'2026-11-23','images':[],'valid':True,'warnings':['原有提示']})
        view=version_out(issue,version)
        assert view['valid'] is False and view['data_contract']['errors']
        assert view['warnings'][0]=='原有提示' and '指数点位' in view['warnings'][1]
        assert version.payload['valid'] is True and version.payload['warnings']==['原有提示']
        assert view['title']==version.title and view['dynamic_body']==version.dynamic_body
