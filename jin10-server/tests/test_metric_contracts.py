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
