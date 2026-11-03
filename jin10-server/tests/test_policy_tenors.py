"""The observed PBC overnight switch must not erase operations or cross tenors."""
import pytest
from app.briefs.central_bank import parse_article
from app.briefs.editorial_cn import build_context


def article(rows):
    return ('<p>2026年10月30日</p><table>'+''.join('<tr>'+''.join('<td>'+c+'</td>' for c in row)+'</tr>' for row in rows)+'</table>').encode()


@pytest.mark.parametrize('term,days,product,spread',[
    ('隔夜',1,'DR001','3.84'),('1天',1,'DR001','3.84'),('7天',7,'DR007','11.35')])
def test_actual_operation_tenor_drives_rule_and_sample_copy(tmp_path,monkeypatch,term,days,product,spread):
    from app.briefs import sources
    from app.briefs.engines.liquidity import render
    from app.briefs.editorial_authorization import adoption
    from app.briefs.skill_production import apply_skill_copy
    bank=parse_article(article([[term,'1.25%','5,410亿元','5,410亿元']]),'2026-10-30')
    assert bank['term_days']==days and bank['awarded_yi']=='5410'
    snapshot={'quotes':{'updated_at':'2026-10-30 17:00','quote_date':'2026-10-30','rows':[
        {'product':'DR001','weighted_rate':'1.2884','latest_rate':None,'updated_at':'2026-10-30 17:00'},
        {'product':'DR007','weighted_rate':'1.3635','latest_rate':None,'updated_at':'2026-10-30 17:00'}]},
        'central_bank':bank,'history':{'start':'2026-10-29','end':'2026-10-30','rows':[
            {'date':'2026-10-29','DR001':'1.29','DR007':'1.40'},
            {'date':'2026-10-30','DR001':'1.2884','DR007':'1.3635'}]}}
    monkeypatch.setattr(render,'render_poster',lambda *a,**k:(tmp_path/'chart.png',None,{}))
    p=sources.render_liquidity(snapshot,tmp_path)
    assert product+'较当日' in p['dynamic_body'] and spread+'个基点' in p['dynamic_body']
    context=build_context('liquidity',p)
    assert context['bindings']['policy']['comparison_product']==product
    apply_skill_copy(p,'liquidity',{'brief_editorial':adoption('test-user')})
    assert p['editorial']['status']=='active'
    assert product+'较当日' in p['dynamic_body'] and spread+'个基点' in p['dynamic_body']
    assert '5410亿元' in p['dynamic_body'] and '净投放' not in p['dynamic_body']


def test_both_tenors_are_preserved_but_duplicate_rows_are_rejected():
    rows=[['7天','1.40%','100亿元','100亿元'],['隔夜','1.25%','5410亿元','5410亿元']]
    result=parse_article(article(rows),'2026-10-30')
    assert result['term_days']==1 and [r['term_days'] for r in result['operations']]==[1,7]
    with pytest.raises(ValueError):parse_article(article(rows+[rows[1]]),'2026-10-30')


@pytest.mark.parametrize('row',[
    ['14天','1.40%','100亿元','100亿元'],['隔夜','1.25%','100亿元','101亿元'],
    ['隔夜','-1.25%','100亿元','100亿元']])
def test_unsupported_or_invalid_operations_do_not_become_policy_facts(row):
    with pytest.raises(ValueError):parse_article(article([row]),'2026-10-30')


@pytest.mark.parametrize('error',[TimeoutError('timeout'),OSError('unavailable')])
def test_optional_announcement_network_failure_has_a_controlled_fallback(monkeypatch,error):
    from app.briefs import central_bank
    def unavailable(*a,**k):raise error
    monkeypatch.setattr(central_bank,'urlopen',unavailable)
    with pytest.raises(ValueError,match='公告请求未完成'):
        central_bank.download(central_bank.INDEX)
