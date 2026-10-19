from datetime import date
from types import SimpleNamespace

from app.briefs.process_details import describe, for_job, safe_value
from app.briefs.sources import save
from app.briefs.service import enqueue, root_for
from tests.conftest import login


def test_process_is_team_scoped_and_reports_saved_inputs_without_touching_chart(client,seed,db_factory):
    with db_factory() as db:
        job=enqueue(db,seed['editor'].team_id,date(2026,10,16),'valuation',user_id=seed['editor'].id)
        db.commit();id=job.id;folder=root_for(job.team_id)/'runs'/str(id)
        payload={'artifact_dir':f'runs/{id}','data_date':'2026-10-15','title':'估值稿',
                 'dynamic_body':'2026-10-15，ERP为6.00%。','images':['chart.png'],
                 'facts':{'erp_latest':{'pe_ttm_mcw':'12.50','bond_yield_number':'2','erp_percent':'6'},
                          'gdp':{'value_yi':'100','source':'https://stats.example/gdp?token=hidden'}}}
        save(folder/'result.json',payload);save(folder/'erp-plot-data.json',[{'date':'2026-10-15','pe_ttm_mcw':'12.50'}])
    auth=login(client,seed['editor'].email)
    response=client.get(f'/api/briefs/jobs/{id}/process',headers=auth)
    assert response.status_code==200
    info=response.json()['details']
    assert any('100 ÷ 12.50 − 2 = 6.00%' in line for line in info['calculations'])
    assert info['result']['body'].startswith('2026年10月15日')
    assert info['evidence'][0]['rows']==1 and len(info['evidence'][0]['sha256'])==64
    assert not (folder/'chart.png').exists()  # Inspection never renders/downloads images.
    assert 'hidden' not in response.text
    assert client.get(f'/api/briefs/jobs/{id}/process',headers=login(client,seed['outsider'].email)).status_code==404
    assert client.get(f'/api/briefs/jobs/{id}/process',headers=login(client,seed['viewer'].email)).status_code==403


def test_cached_result_links_original_evidence_and_missing_result_is_not_completion(tmp_path):
    job=SimpleNamespace(id='retry',category='volume',action='produce')
    result={'artifact_dir':'runs/first','facts':{'amount_yi':'16300','ma20_yi':'17000'},'images':['a.png']}
    save(tmp_path/'runs/retry/result.json',result)
    save(tmp_path/'runs/first/plot-data.json',[{'date':'2026-10-15','turn':'16300'}])
    report=for_job(job,tmp_path)
    assert report['reused'] and report['available']
    assert report['evidence'][0]['rows']==1
    assert any('1.63 万亿元' in line for line in report['calculations'])
    missing=for_job(SimpleNamespace(id='not-started',category='volume',action='produce'),tmp_path)
    assert not missing['available'] and not missing['reused'] and not missing['evidence']


def test_future_adapters_share_explanations_and_secrets_are_not_exposed():
    payload={'facts':{'value':'1','api_key':'hidden','request_headers':{'Authorization':'Bearer hidden'},
                      'url':'https://example.org/data?access_token=hidden'},
             'process_details':{'sources':[{'label':'官方数据','url':'https://example.org/data'}],
                                'inputs':[{'label':'本期数值','value':'1','unit':'%'}],
                                'calculations':['当期值减前期值。']}}
    result=describe('future_product',payload)
    assert result['calculations']==['当期值减前期值。']
    assert result['inputs'][0]['label']=='本期数值'
    assert result['sources'][0]['url']=='https://example.org/data'
    assert 'hidden' not in str(result)
    assert safe_value({'source':'https://name:password@example.org/private'})=={'source':None}


def test_turnover_uses_counts_to_reproduce_nine_day_mean(tmp_path):
    job=SimpleNamespace(id='one',category='turnover',action='produce')
    folder=tmp_path/'runs/one'
    save(folder/'result.json',{'artifact_dir':'runs/one','facts':{'low_ma9':'60.00','high_ma9':'20.00'}})
    save(folder/'source.json',[{'date':f'2026-10-{day:02d}','totalCompany':100,'belowPercent3':60,'belowPercent5':80} for day in range(1,10)])
    assert any('低换手 60.00%，高换手 20.00%' in x for x in for_job(job,tmp_path)['calculations'])
