"""Shared semantic contracts for computation, adoption and exported values.

An index level is not a currency amount. Matching a historical sample does not
prove a conversion factor; a source adapter must provide the correct quantity.
"""
from dataclasses import dataclass
from decimal import Decimal

from .failures import SourceContractError


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str
    meaning: str

    def __post_init__(self):
        number=Decimal(str(self.value))
        if not number.is_finite() or number<=0:
            raise SourceContractError('指标输入必须为有限正数',source='metric_contract')
        object.__setattr__(self,'value',number)


def market_cap_to_gdp(market_cap: Quantity,gdp: Quantity):
    if (market_cap.meaning!='market_capitalization' or gdp.meaning!='nominal_gdp'
            or market_cap.unit!=gdp.unit or market_cap.unit not in ('CNY_100_million','USD_billion')):
        raise SourceContractError('巴菲特指数输入口径不符：分子须为真实市值，分母须为同币种、同数量级的名义GDP；'
            '指数点位不能直接作为美元市值',source='market_cap_to_gdp_contract')
    return market_cap.value/gdp.value


SOURCE_QUANTITIES={
    'eastmoney_all_a_total_market_cap':('CNY_100_million','market_capitalization'),
    'nbs_annual_nominal_gdp':('CNY_100_million','nominal_gdp'),
    'yahoo_w5000':('index_points','index_level'),
    'fred_nominal_gdp':('USD_billion','nominal_gdp'),
}
BUFFETT_INPUTS={
    'valuation':('eastmoney_all_a_total_market_cap','nbs_annual_nominal_gdp'),
    'us_barometer':('yahoo_w5000','fred_nominal_gdp'),
}


def production_contract(category,payload):
    """Re-evaluate a saved edition; never trust its old `valid` flag alone."""
    errors=[];inputs={}
    if category in BUFFETT_INPUTS:
        numerator,denominator=BUFFETT_INPUTS[category]
        inputs={'numerator':{'method':numerator,'unit':SOURCE_QUANTITIES[numerator][0]},
                'denominator':{'method':denominator,'unit':SOURCE_QUANTITIES[denominator][0]}}
        try:market_cap_to_gdp(Quantity(1,*SOURCE_QUANTITIES[numerator]),Quantity(1,*SOURCE_QUANTITIES[denominator]))
        except SourceContractError as exc:errors.append(str(exc))
    return {'version':1,'status':'unverified' if errors else 'validated_by_adapter',
            'errors':errors,'inputs':inputs}


def require_production_contract(category,payload):
    report=production_contract(category,payload)
    if report['errors']:
        raise SourceContractError(report['errors'][0]+'；已采集数据保留，禁止生成或确认冒充精确口径的稿件',
                                  source='market_cap_to_gdp_contract')
    return report
