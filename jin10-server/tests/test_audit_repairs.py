"""Regression cases from the project audit, using no live providers or private network."""
import io
import socket
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.briefs.body_templates import validate, render, TemplateError
from app.briefs.skill_ai import validate_answer
from app.briefs import ai_compute_import
from app.briefs.process_details import for_job
from app.briefs.sources import save
from app.db.models import AIComputeObservation, AIComputeRevision, TeamBusinessRevision
from app.news_skill_os import link_ingest
from app.services import data_export, live_catalog
from tests.conftest import login


@pytest.mark.parametrize('bad', ['A为20%，B为10%。','A为10%，B为20%，建议买入。','A下降10%，B上涨20%。'])
def test_ai_cannot_swap_indicators_invert_direction_or_add_claims(bad):
    with pytest.raises(ValueError):
        validate_answer('市场指标','A为10%，B为20%。',{'title':'市场指标','body':bad})


@pytest.mark.parametrize('template', ['本期成交额999万亿元。','{date}成交额999万亿元。','{amount_yi:0>1000000}','{amount_yi!r}','{}'])
def test_templates_reject_fixed_observations_and_format_expansion(template):
    with pytest.raises(TemplateError):validate('volume',template)


def test_equal_threshold_and_missing_template_inputs():
    payload={'facts':{'low_ma9':'60','previous_low_ma9':'61','high_ma9':'17','previous_high_ma9':'16'}}
    assert render('turnover','{threshold_side}{high_ma9}%',payload)=='等于17%'
    with pytest.raises(TemplateError):render('volume','{amount_yi}亿元',{'facts':{}})


def test_redirect_is_checked_before_internal_request(monkeypatch):
    seen=[]
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,port,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1' if host=='127.0.0.1' else '93.184.216.34',port))])
    original=httpx.Client
    def client(**kwargs):
        kwargs['transport']=httpx.MockTransport(handler)
        return original(**kwargs)
    monkeypatch.setattr(httpx,'Client',client)
    with pytest.raises(link_ingest.LinkIngestError):link_ingest.fetch_article('https://public.example/story')
    assert seen==['https://public.example/story']


def test_transport_pins_validated_ip_and_keeps_tls_hostname(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    seen=[]
    def send(self,request):
        seen.append((request.url.host,request.headers['host'],request.extensions['sni_hostname']))
        return httpx.Response(200)
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',send)
    with link_ingest.PublicTransport() as transport:
        transport.handle_request(httpx.Request('GET','https://public.example/story'))
    assert seen==[('93.184.216.34','public.example','public.example')]


def test_gpu_dates_match_ticks_and_missing_values_remain_gaps():
    from app.briefs.ai_compute_charts import gpu_chart,plt
    import matplotlib.dates as dates
    import math
    rows=[{'date':f'2026-10-0{i}','a100':None if i==2 else i,'h100':2,'b200':3} for i in (1,2,3)]
    with patch('matplotlib.figure.Figure.savefig'),patch('matplotlib.pyplot.close'):
        gpu_chart(rows,Path('.'));ax=plt.gcf().axes[0]
        expected=[dates.date2num(date.fromisoformat(r['date'])) for r in rows]
        for line in ax.lines:assert line.get_xdata(orig=False).tolist()==expected
        assert math.isnan(ax.lines[0].get_ydata()[1])
        assert list(ax.get_xticks())==expected
    plt.close('all')


def test_compute_import_retains_both_values_and_export_is_team_scoped(client,db_factory,seed):
    team=seed['editor'].team_id
    with db_factory() as db:
        ai_compute_import._upsert(db,team,'token',[{'date':date(2026,10,22),'token_price':1}])
        ai_compute_import._upsert(db,team,'token',[{'date':date(2026,10,22),'token_price':2}])
        db.commit()
        revisions=list(db.scalars(select(AIComputeRevision).order_by(AIComputeRevision.revision)))
        assert [r.values['token_price'] for r in revisions]==['1','2']
        assert len(list(db.scalars(select(AIComputeObservation))))==1
    result=client.get('/api/export/ai_compute_revisions',params={'format':'json'},headers=login(client,seed['editor'].email))
    assert len(result.json())==2
    assert client.get('/api/export/ai_compute_revisions',params={'format':'json'},headers=login(client,seed['outsider'].email)).json()==[]


def test_business_settings_are_versioned_exportable_and_secret_free(client,db_factory,seed):
    auth=login(client,seed['admin'].email)
    assert client.put('/api/settings',headers=auth,json={'revision':0,'brief_templates':{'volume':{'use_builtin':False,'template':'{amount_yi}亿元'}},'ai':{'deepseek':{'key':'private-test-key'}}}).status_code==200
    response=client.get('/api/export/team_business_revisions',headers=login(client,seed['viewer'].email),params={'format':'json'})
    assert response.status_code==200 and response.json()
    assert 'amount_yi' in response.text and 'private-test-key' not in response.text and 'token' not in response.text


def test_export_formula_is_text_and_stream_size_limit_is_enforced(tmp_path,monkeypatch):
    dataset=data_export.MATERIALS
    path=data_export.write_export(dataset,[{'title':'=1+1'}],{},'xlsx')
    try:
        book=load_workbook(path,data_only=False)
        assert all(cell.data_type!='f' for row in book.active for cell in row)
        assert any(cell.value=='=1+1' for row in book.active for cell in row)
    finally:Path(path).unlink()
    assert "'=1+1" in data_export.csv_bytes(dataset,[{'title':'=1+1'}],{}).decode('utf-8-sig')
    monkeypatch.setattr(data_export,'MAX_EXPORT_BYTES',200)
    with pytest.raises(data_export.ExportLimitError):data_export.write_export(dataset,[{'title':'a'*1000}],{},'json')


def test_final_process_uses_saved_skill_result_and_rule_copy(tmp_path):
    job=SimpleNamespace(id='one',category='volume',action='produce')
    save(tmp_path/'runs/one/result.json',{'artifact_dir':'runs/one','title':'最终标题','dynamic_body':'最终正文',
        'rule_copy':{'title':'规则标题','body':'规则正文'},'skill':{'status':'ok','provider':'openai','model':'configured-model'}})
    details=for_job(job,tmp_path)
    assert details['result']['title']=='最终标题' and details['rule_copy']['body']=='规则正文'
    assert details['result']['ai_provider']=='openai' and details['result']['ai_status']=='ok'


def test_invalid_chat2api_model_and_historical_generation_live_policy(client,seed,db_factory,monkeypatch):
    from app.services.team_settings import chat2api_supported,chat2api_effective
    from app.briefs.service import enqueue
    from app.briefs.policy import CHINA
    assert not chat2api_supported('gpt-4-not-a-real-model')
    assert chat2api_effective('gpt-4-not-a-real-model')=='gpt-4o'
    monkeypatch.setattr(live_catalog,'now',lambda:datetime(2026,10,23,10,0,tzinfo=CHINA))
    with db_factory() as db:
        enqueue(db,seed['editor'].team_id,date(2026,10,22),'volume',user_id=seed['editor'].id);db.commit()
    assert client.get('/api/live/state',headers=login(client,seed['editor'].email)).json()['busy']


def test_daily_edits_require_a_version_and_reject_lost_updates(client,seed,db_factory):
    from app.db.models import Material
    auth=login(client,seed['editor'].email)
    with db_factory() as db:
        row=Material(id=uuid.uuid4(),team_id=seed['editor'].team_id,title='原文',content='内容',content_raw='内容',item_kind='flash',interpretation_status='none',
            source='jin10',fingerprint='a'*64,images=[],tags=[],raw_html='',url='https://example.org',
            collect_time=datetime.now(timezone.utc),collect_method='context_menu',created_by=seed['editor'].id,collected_by=[])
        db.add(row);db.commit();id=row.id
    url=f'/api/materials/{id}/edits'
    stamp=client.get(f'/api/materials/{id}',headers=auth).json()['updated_at']
    assert client.put(url,headers=auth,json={'title':'甲'}).status_code==428
    assert client.put(url,headers={**auth,'If-Match':stamp},json={'title':'甲'}).status_code==200
    assert client.put(url,headers={**auth,'If-Match':stamp},json={'title':'乙'}).status_code==409
    assert client.delete(url,headers={**auth,'If-Match':stamp}).status_code==409
