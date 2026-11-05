"""Behavioral regressions for the November 5 audit. Isolated fixtures only."""
import copy,hashlib,io,json,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timezone
from pathlib import Path
from threading import Barrier,Event
import pytest
from sqlalchemy import select
from app.db.models import Asset,BriefIssue,BriefJob,BriefVersion,Material,Team
from app.briefs.service import append_version,ensure_issue,enqueue,root_for,edition_payload
from tests.conftest import login


def edition():
    return {'title':'Original generation','dynamic_body':'Original facts','data_date':'2026-11-04',
            'facts':{},'fixed_notes':'saved notes','images':[],'image_sha256':{},
            'artifact_dir':'runs/original','valid':True,'warnings':[]}


def test_generation_preserves_an_edit_saved_while_running(db_factory,seed,monkeypatch):
    from app.briefs import worker
    team=seed['editor'].team_id;day=date(2026,11,5)
    with db_factory() as db:
        issue=ensure_issue(db,team,day,'volume');append_version(db,issue,edition())
        issue_id=issue.id;j=enqueue(db,team,day,'volume',action='refresh',user_id=seed['editor'].id)
        j.status='running';j.attempts=1;db.commit();job_id=j.id
    class Process:
        def __init__(self,*a,**k):
            with db_factory() as db:
                issue=db.get(BriefIssue,issue_id)
                append_version(db,issue,edition(),kind='edited',title='NEW EDITOR TITLE',body='NEW EDITOR BODY')
                db.commit()
            out=root_for(team)/'runs'/str(job_id)
            p=edition();p['artifact_dir']='runs/'+str(job_id)
            (out/'result.json').write_text(json.dumps(p),encoding='utf-8')
        def poll(self):return 0
    monkeypatch.setattr(worker.subprocess,'Popen',Process)
    monkeypatch.setattr('app.briefs.skill_production.apply_skill_copy',lambda *a:None)
    worker.execute(db_factory,job_id)
    with db_factory() as db:
        i=db.get(BriefIssue,issue_id)
        latest=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==i.id,BriefVersion.number==i.version))
        assert i.version==2 and latest.title=='NEW EDITOR TITLE' and latest.kind=='edited'
        assert db.get(BriefJob,job_id).status=='failed'


def test_liquidity_digest_covers_policy_and_dr014_corrections(tmp_path):
    from app.briefs.product_inputs import publication_digest
    raw={'quotes':{'quote_date':'2026-11-04','rows':[
        {'product':'DR001','weighted_rate':'1.2884'}, {'product':'DR007','weighted_rate':'1.3635'}]},
        'history':{'rows':[{'date':'2026-11-04','DR001':'1.2884','DR007':'1.3635','DR014':'1.5'}]},
        'central_bank':{'date':'2026-11-04','term_days':1,'rate_percent':'1.25','awarded_yi':'5410','bid_yi':'5410'}}
    path=tmp_path/'liquidity-snapshot.json';path.write_text(json.dumps(raw))
    p={'data_date':'2026-11-04'};before=publication_digest('liquidity',tmp_path,p)
    raw['central_bank'].update(rate_percent='1.30',awarded_yi='8000',bid_yi='8000')
    raw['history']['rows'][0]['DR014']='2.5';path.write_text(json.dumps(raw))
    assert before!=publication_digest('liquidity',tmp_path,p)


def test_compute_digest_covers_published_weekly_change(tmp_path):
    from app.briefs.product_inputs import publication_digest
    rows=[{'date':'2026-11-04','token_price':'0.89','status':'public_display',
           'public_display':{'readings':[{'metric':'token','date':'2026-11-04','price':'0.89','week_change_pct':'-6.8'}]}}]
    path=tmp_path/'ai-compute-source.json';path.write_text(json.dumps(rows));p={'data_date':'2026-11-04'}
    before=publication_digest('token_cost',tmp_path,p)
    rows[0]['public_display']['readings'][0]['week_change_pct']='-9.9'
    path.write_text(json.dumps(rows));assert before!=publication_digest('token_cost',tmp_path,p)


@pytest.mark.parametrize('db_factory',['independent_connections'],indirect=True)
def test_concurrent_asset_upload_preserves_file_and_database_identity(client,db_factory,seed,monkeypatch):
    from PIL import Image
    from app.api import assets
    from app.services.storage import asset_path
    headers=login(client,'editor@plus.test');url='https://example.com/revised.png'
    aid=assets.expected_asset_id(url);buffers=[]
    for color in ('red','blue'):
        b=io.BytesIO();Image.new('RGB',(3,3),color).save(b,format='PNG');buffers.append(b.getvalue())
    from contextlib import contextmanager
    first,second=buffers;barrier=Barrier(2);lock=assets.upload_lock
    @contextmanager
    def racing_lock(*args):
        barrier.wait(timeout=10)
        with lock(*args):yield
    monkeypatch.setattr(assets,'upload_lock',racing_lock)
    def send(data):
        return client.put('/api/assets/'+aid,headers={**headers,'Content-Type':'image/png','X-Source-Url':url},content=data).status_code
    with ThreadPoolExecutor(2) as executor:
        a=executor.submit(send,first);b=executor.submit(send,second)
        assert sorted([a.result(15),b.result(15)])==[200,201]
    with db_factory() as db:
        asset=db.get(Asset,(seed['editor'].team_id,aid))
        assert hashlib.sha256(asset_path(seed['editor'].team_id,aid).read_bytes()).hexdigest()==asset.sha256


def test_review_rejects_a_concurrently_revised_draft(db_factory,seed):
    from app.news_skill_os.store import create_draft,get_draft,add_draft_revision,review_draft,list_draft_revisions,StoreConflict
    user=seed['editor'];team=user.team_id
    with db_factory() as db:
        draft=create_draft(db,team_id=team,user=user,title='v1',body='body1');db.commit();ident=draft.id
    with db_factory() as review_db:
        stale=get_draft(review_db,team,ident)
        with db_factory() as edit_db:
            current=get_draft(edit_db,team,ident)
            add_draft_revision(edit_db,draft=current,user=user,expected_revision=1,title='UNREVIEWED',body='body2');edit_db.commit()
        with pytest.raises(StoreConflict):review_draft(review_db,draft=stale,user=user,decision='approve',expected_revision=1)
        review_db.rollback()
    with db_factory() as db:
        current=get_draft(db,team,ident);revisions=list_draft_revisions(db,team,ident)
        assert current.current_revision==2 and current.status=='draft'
        assert revisions[-1].review_status=='pending' and revisions[0].review_status=='pending'


def test_stale_jev_result_preserves_requested_reanalysis(client,db_factory,seed):
    from tests.test_api import material,push
    from tests.test_ai import answers
    from app.ai.worker import analyze_one
    row=push(client,login(client,'editor@plus.test'),material())[0]['material'];ident=uuid.UUID(row['id'])
    with db_factory() as db:m=db.get(Material,ident);m.ai_status='analyzing';db.commit()
    class Client:
        def evaluate(self,state,questions):
            with db_factory() as db:
                m=db.get(Material,ident);m.content='NEW SOURCE CONTENT';m.ai_status='pending';db.commit()
            return answers(section='ai')
    assert analyze_one(db_factory,Client(),ident,3)=='stale'
    with db_factory() as db:
        m=db.get(Material,ident);assert m.content=='NEW SOURCE CONTENT' and m.ai_status=='pending'


@pytest.mark.parametrize('corrupt',[False,True])
def test_optional_ai_config_never_blocks_rule_generation(db_factory,seed,monkeypatch,corrupt):
    from app.config import get_settings
    from app.services.team_settings import cipher
    from app.briefs.worker import execute
    token='damaged-token' if corrupt else cipher().encrypt(b'fixture-not-a-real-key').decode()
    with db_factory() as db:
        team=db.get(Team,seed['editor'].team_id);team.settings={'ai':{'deepseek':{'token':token,'model':'fixture-model'}}}
        j=enqueue(db,team.id,date(2026,11,5),'volume',user_id=seed['editor'].id)
        j.status='running';j.attempts=1;db.commit();ident=j.id
    monkeypatch.setattr(get_settings(),'jwt_secret','rotated-test-only-key-0123456789')
    from app.services.team_settings import provider
    with db_factory() as db:
        config=provider(db.get(Team,seed['editor'].team_id),'deepseek')
        assert config['key']==('' if corrupt else 'fixture-not-a-real-key')
        assert bool(config.get('configuration_error')) is corrupt
    from app.briefs import worker
    class Process:
        def __init__(self,*a,**k):
            out=root_for(seed['editor'].team_id)/'runs'/str(ident)
            p=edition();p['artifact_dir']='runs/'+str(ident)
            (out/'result.json').write_text(json.dumps(p),encoding='utf-8')
        def poll(self):return 0
    monkeypatch.setattr(worker.subprocess,'Popen',Process)
    monkeypatch.setattr('app.briefs.skill_production.apply_skill_copy',lambda *a:None)
    execute(db_factory,ident)
    with db_factory() as db:
        j=db.get(BriefJob,ident);assert j.status=='succeeded' and j.error is None


def test_password_reset_revokes_old_login(client,db_factory,seed,monkeypatch):
    from argparse import Namespace
    from app import cli
    headers=login(client,'editor@plus.test')
    monkeypatch.setattr(cli,'_read_password',lambda:'replacement-test-password')
    with db_factory() as db:cli.reset_password(db,Namespace(email='editor@plus.test'))
    assert client.get('/api/auth/me',headers=headers).status_code==401
    fresh=client.post('/api/auth/login',json={'email':'editor@plus.test','password':'replacement-test-password'})
    assert fresh.status_code==200
    assert client.get('/api/auth/me',headers={'Authorization':'Bearer '+fresh.json()['access_token']}).status_code==200


def test_health_requires_recent_issuer_verification_for_oil(db_factory,seed):
    from app.services.production_health import production_status
    from app.db.models import BriefConfig
    from app.briefs.policy import CHINA
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,1,5),'oil_etf')
        p=edition();p['data_date']='2026-01-02';append_version(db,issue,p)
        db.add(BriefConfig(team_id=seed['editor'].team_id,enabled=True,data_enabled=True));db.commit()
        report=production_status(db,seed['editor'].team_id,datetime(2026,11,5,12,tzinfo=CHINA))
    oil=next(r for r in report['items'] if r['category']=='oil_etf')
    assert oil['status']=='verification_stale' and any(a.get('category')=='oil_etf' for a in report['alerts'])


def test_gpu_wrong_order_falls_back_to_sample_structure(monkeypatch):
    from app.briefs.editorial_authorization import adoption
    from app.briefs.skill_production import apply_skill_copy
    from app.briefs import gpu_editorial_ai
    cases=json.loads((Path(__file__).parent/'fixtures/editorial_suite_cases.json').read_text(encoding='utf-8'))['cases']
    p=copy.deepcopy(next(c['payload'] for c in cases if c['category']=='gpu_rental' and c['expect']['status']=='ok'))
    def wrong_order(settings,context):
        assert 'B200' in context['writing_order']
        return {'status':'ok','plan':{'headline':context['headlines'][0]['id'],'paragraphs':[['a100.0','h100.0','b200.0']]}}
    monkeypatch.setattr(gpu_editorial_ai,'select_plan',wrong_order)
    apply_skill_copy(p,'gpu_rental',{'brief_editorial':adoption('fixture-user')})
    assert p['editorial']['status']=='active' and p['editorial']['candidate']['hard_checks']['structure'] is True
    assert p['dynamic_body'].index('B200')<p['dynamic_body'].index('H100')<p['dynamic_body'].index('A100')
    assert p['editorial']['selection_status']=='deterministic_fallback'


def test_valuation_collection_completes_without_an_image(tmp_path,monkeypatch):
    from app.briefs import sources,valuation_repair
    from app.briefs.engines.valuation import run_erp,fetch_free_all_a
    out=tmp_path/'out';state=tmp_path/'state';out.mkdir();state.mkdir()
    for name in ('erp-history.json','bond-history.json','buffett-history.json'):
        sources.save(state/name,[])
    monkeypatch.setenv('DATA_COLLECTION_ONLY','1')
    monkeypatch.setattr(valuation_repair,'available_pe',lambda *a,**k:{})
    monkeypatch.setattr(valuation_repair,'repair_buffett',lambda *a,**k:{})
    monkeypatch.setattr(valuation_repair,'repair_erp',lambda *a,**k:{'restored_dates':[]})
    def erp(history,folder,**kwargs):
        assert kwargs['data_only'] is True
        sources.save(folder/'数据快照.json',[{'date':d,'close':'4000','erp_percent':'6'} for d in ('2026-11-03','2026-11-04')])
        return {'data_ready':True,'release_ready':False,'image_generated':False}
    monkeypatch.setattr(run_erp,'run_erp',erp)
    monkeypatch.setattr(fetch_free_all_a,'fetch_with_archive',lambda *a,**k:{'rows':[{'date':d,'market_cap_yi':'1000000'} for d in ('2026-11-03','2026-11-04')],'source':'fixture','raw_evidence':[]})
    result=sources.valuation('2026-11-04',out,state)
    assert result['data_date']=='2026-11-04' and result['images']==[] and result['facts']['erp_latest']
    assert (out/'erp-plot-data.json').is_file()


def test_xlsx_export_retains_long_field_in_companion_sheet():
    from app.services.data_export import Dataset,Column,xlsx_bytes,json_bytes
    from openpyxl import load_workbook
    d=Dataset(key='fixture',label='fixture',description='',date_label='',day=False,columns=(Column('body','body'),),count=lambda *a:1,rows=lambda *a:[])
    body='a'*40000
    exported=load_workbook(io.BytesIO(xlsx_bytes(d,[{'body':body}],{})),read_only=True)
    cell=exported.active.cell(2,1).value
    assert '长字段' in cell
    assert ''.join(row[5] for row in exported['长字段'].iter_rows(min_row=2,values_only=True))==body
    assert len(json.loads(json_bytes(d,[{'body':body}],{}))[0]['body'])==40000


def test_failed_source_check_cannot_hide_a_known_correction(client,db_factory,seed):
    from app.db.models import BriefDataCheck
    team=seed['editor'].team_id
    with db_factory() as db:
        issue=ensure_issue(db,team,date(2026,11,5),'volume');p=edition()
        p['data_reliability']={'publication_digest':'old-source','publication_digest_version':2}
        append_version(db,issue,p);ident=issue.id
        db.add(BriefDataCheck(team_id=team,category='volume',data_date=date(2026,11,4),status='complete',report={'current_data_valid':True,'publication_digest':'corrected-source','publication_digest_version':2},created_at=datetime(2026,11,5,0,tzinfo=timezone.utc)))
        db.commit()
    headers=login(client,'editor@plus.test')
    assert client.post(f'/api/briefs/issues/{ident}/confirm',json={'version':1},headers=headers).status_code==409
    with db_factory() as db:
        db.add(BriefDataCheck(team_id=team,category='volume',data_date=date(2026,11,4),status='failed',report={'current_data_valid':False,'error':'fixture-source-temporarily-unavailable'},created_at=datetime(2026,11,5,1,tzinfo=timezone.utc)))
        db.commit()
    assert client.post(f'/api/briefs/issues/{ident}/confirm',json={'version':1},headers=headers).status_code==409


@pytest.mark.parametrize('corrected',[False,True])
def test_legacy_publication_gate_uses_immutable_archived_policy(client,db_factory,seed,tmp_path,corrected):
    from app.briefs.product_inputs import publication_digest
    from app.briefs.source_archive import archive_run
    from app.db.models import BriefDataCheck
    raw={'quotes':{'quote_date':'2026-11-04','rows':[{'product':'DR001','weighted_rate':'1.2'},
        {'product':'DR007','weighted_rate':'1.3'}]},'history':{'rows':[]},
        'central_bank':{'date':'2026-11-04','term_days':1,'rate_percent':'1.25','awarded_yi':'100','bid_yi':'100'}}
    team=seed['editor'].team_id;old=root_for(team)/'runs'/'legacy';old.mkdir(parents=True)
    (old/'liquidity-snapshot.json').write_text(json.dumps(raw))
    p=edition();p['artifact_dir']='runs/legacy'
    p['data_reliability']={'publication_digest':publication_digest('liquidity',old,p,version=2),'publication_digest_version':2}
    if corrected:raw['central_bank']['rate_percent']='1.30'
    capture=tmp_path/'capture';capture.mkdir();(capture/'liquidity-snapshot.json').write_text(json.dumps(raw))
    with db_factory() as db:
        issue=ensure_issue(db,team,date(2026,11,5),'liquidity');append_version(db,issue,p);ident=issue.id
        job=enqueue(db,team,date(2026,11,5),'liquidity',action='reconcile');job.attempts=1
        job.status='succeeded';job.active_key=None;db.flush();archive_run(db,job,capture)
        legacy_hash=publication_digest('liquidity',capture,p,version=2)
        assert legacy_hash==p['data_reliability']['publication_digest']
        db.add(BriefDataCheck(team_id=team,category='liquidity',data_date=date(2026,11,4),job_id=job.id,attempt=1,status='complete',
            report={'current_data_valid':True,'publication_digest':legacy_hash,'publication_digest_version':2}));db.commit()
    (capture/'liquidity-snapshot.json').unlink()
    result=client.post(f'/api/briefs/issues/{ident}/confirm',json={'version':1},headers=login(client,'editor@plus.test'))
    assert result.status_code==(409 if corrected else 200),result.text


def test_compute_backfill_retains_display_facts_for_publication_gate(db_factory,seed,monkeypatch):
    from app.briefs import worker
    from app.db.models import AIComputeObservation
    display={'readings':[{'metric':'token','date':'2026-11-04','price':'0.89','week_change_pct':'-6.8'}]}
    with db_factory() as db:
        db.add(AIComputeObservation(team_id=seed['editor'].team_id,kind='token',date=date(2026,11,4),token_price='0.89',
            source='fixture',source_status='public_visible_summary',public_display=display))
        j=enqueue(db,seed['editor'].team_id,date(2026,11,5),'token_cost',action='reconcile');j.status='running';j.attempts=1
        db.commit();ident=j.id
    monkeypatch.setattr(worker,'capture_public_for_job',lambda *a:{'data_date':'2026-11-04'})
    worker.execute(db_factory,ident)
    rows=json.loads((root_for(seed['editor'].team_id)/'runs'/str(ident)/'ai-compute-source.json').read_text())
    assert rows[0]['public_display']==display
    with db_factory() as db:assert db.get(BriefJob,ident).status=='succeeded'
