"""Cross-operation regressions from the November full-project audit."""
import copy,hashlib,json
from datetime import date,datetime,timezone
from pathlib import Path
from sqlalchemy import select
import pytest
from app.db.models import BriefVersion,BriefJob,BriefSourceBlob,BriefSourceSnapshot
from app.briefs.service import ensure_issue,append_version,edition_payload,root_for,enqueue


def payload(folder='runs/source'):
    return {'title':'旧标题','dynamic_body':'旧正文','data_date':'2026-11-02','facts':{},'fixed_notes':'',
        'images':[],'image_sha256':{},'artifact_dir':folder,'valid':True}


def test_legacy_edited_edition_replay_reads_editorial_columns(db_factory,seed):
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,11,3),'oil_etf')
        v=append_version(db,issue,payload(),title='编辑标题',body='编辑正文')
        assert v.payload['title']==v.title=='编辑标题' and v.payload['dynamic_body']==v.dynamic_body=='编辑正文'
        v.payload={**v.payload,'title':'历史旧快照','dynamic_body':'历史旧快照'}
        assert edition_payload(v)['dynamic_body']=='编辑正文'


def test_refresh_renders_the_saved_edition_notes_not_changed_defaults(db_factory,seed,monkeypatch):
    from app.briefs import worker
    from app.db.models import BriefConfig
    team=seed['editor'].team_id;day=date(2026,11,3)
    with db_factory() as db:
        issue=ensure_issue(db,team,day,'liquidity');p=payload();p['fixed_notes']='编辑保留的图内注释'
        append_version(db,issue,p);db.add(BriefConfig(team_id=team,notes={'liquidity':'后来修改的团队默认注释'}))
        job=enqueue(db,team,day,'liquidity',action='refresh',user_id=seed['editor'].id)
        job.status='running';job.attempts=1;db.commit();job_id=job.id
    class Process:
        def __init__(self,*args,**kwargs):
            rendered_notes=kwargs['env']['CATEGORY_NOTES']
            assert rendered_notes=='编辑保留的图内注释'
            out=root_for(team)/'runs'/str(job_id);p=payload('runs/'+str(job_id))
            p['fixed_notes']=rendered_notes;p['facts']={'rendered_notes':rendered_notes}
            (out/'result.json').write_text(json.dumps(p),encoding='utf-8')
        def poll(self):return 0
    monkeypatch.setattr(worker.subprocess,'Popen',Process)
    monkeypatch.setattr('app.briefs.skill_production.apply_skill_copy',lambda *a:None)
    worker.execute(db_factory,job_id)
    with db_factory() as db:
        assert db.get(BriefJob,job_id).status=='succeeded'
        current=db.scalar(select(BriefVersion).where(BriefVersion.issue_id==issue.id).order_by(BriefVersion.number.desc()))
        assert current.payload['fixed_notes']==current.payload['facts']['rendered_notes']=='编辑保留的图内注释'


def test_retirement_preserves_shared_images_and_rollbacks(db_factory,seed):
    with db_factory() as db:
        team=seed['editor'].team_id;issue=ensure_issue(db,team,date(2026,11,3),'liquidity')
        folder=root_for(team)/'delivery'/'shared';folder.mkdir(parents=True);image=folder/'a.png';image.write_bytes(b'image')
        p={**payload('delivery/shared'),'images':['a.png'],'image_sha256':{'a.png':hashlib.sha256(b'image').hexdigest()}}
        for i in range(4):append_version(db,issue,p,title='修改'+str(i))
        db.commit();assert image.exists()
        assert len(list(db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id))))==2
        append_version(db,issue,payload('runs/new1'));append_version(db,issue,payload('runs/new2'))
        assert image.exists();db.rollback();assert image.exists()
        append_version(db,issue,payload('runs/new3'));append_version(db,issue,payload('runs/new4'))
        assert image.exists();db.commit();assert not image.exists()


@pytest.mark.parametrize('category,series,method,filename,key',[
 ('valuation','erp','verified_erp','erp-history.json','erp_percent'),
 ('oil_etf','BNO','issuer_futures_and_swap_estimate_v1','oil-history.json','barrels'),
 ('liquidity','DR001','cfets_dr_close_v1','dr-history.json','weighted_rate')])
def test_db_revision_overrides_stale_working_file(db_factory,seed,tmp_path,category,series,method,filename,key):
    from app.briefs.data_reliability import save_points
    from app.briefs.repair import restore_working_history
    day='2026-11-02';old={'date':day,key:'1.00'}
    before=[old] if category=='valuation' else {day:{series:old}} if category=='oil_etf' else {'rows':[{'date':day,'DR001':'1.00'}]}
    (tmp_path/filename).write_text(json.dumps(before),encoding='utf-8')
    with db_factory() as db:
        save_points(db,seed['editor'].team_id,category,[{'date':day,'series':series,'method':method,'values':{'date':day,key:'2.00'}}])
        restore_working_history(db,seed['editor'].team_id,category,tmp_path)
    after=json.loads((tmp_path/filename).read_text())
    assert (after[0][key] if category=='valuation' else after[day][series][key] if category=='oil_etf' else after['rows'][0]['DR001'])=='2.00'


def test_manual_retry_preserves_automatic_provenance(db_factory,seed):
    with db_factory() as db:
        j=enqueue(db,seed['editor'].team_id,date(2026,11,3),'us_barometer');j.status='waiting'
        retry=enqueue(db,j.team_id,j.issue_date,j.category,user_id=seed['editor'].id,unique=True)
        assert retry.id==j.id and retry.schedule_origin and retry.created_by==seed['editor'].id


def test_working_revision_stays_with_its_declared_method(db_factory,seed,tmp_path):
    from app.briefs.data_reliability import save_points
    from app.briefs.repair import restore_working_history
    old={'date':'2026-11-02','marketCap':'1','gdp':'1','close':'1','source':'source-a'}
    (tmp_path/'buffett-history.json').write_text(json.dumps([old]))
    with db_factory() as db:
        for method,value in [('source-a','2'),('source-b','3')]:
            save_points(db,seed['editor'].team_id,'valuation',[{'date':old['date'],'series':'buffett','method':method,'values':{**old,'marketCap':value,'source':method}}])
        restore_working_history(db,seed['editor'].team_id,'valuation',tmp_path)
    result=json.loads((tmp_path/'buffett-history.json').read_text())
    assert result[0]['marketCap']=='2' and result[0]['source']=='source-a'


def test_original_bytes_are_deduplicated_per_team_and_restorable(db_factory,seed,tmp_path):
    from app.briefs.source_archive import archive_run
    raw=b'<html>immutable original evidence</html>'*100
    (tmp_path/'source.html').write_bytes(raw)
    with db_factory() as db:
        for n in range(2):
            job=BriefJob(team_id=seed['editor'].team_id,issue_date=date(2026,11,3),category='oil_etf',dedupe_key=str(n),attempts=1)
            db.add(job);db.flush();archive_run(db,job,tmp_path)
        db.commit()
        blobs=list(db.scalars(select(BriefSourceBlob)));sources=list(db.scalars(select(BriefSourceSnapshot)))
        assert len(blobs)==1 and len(sources)==2
        db.expire_all()
        assert all(s.content==raw and s.sha256==hashlib.sha256(raw).hexdigest() for s in sources)


def test_unplotted_old_capture_change_does_not_invalidate_current_product(tmp_path):
    from app.briefs.product_inputs import publication_digest
    (tmp_path/'plot-data.json').write_text(json.dumps([{'date':'2026-11-02','turn':'17800','vol':'100'}]))
    p=payload();values=[]
    for amount in ('1','2'):
        (tmp_path/'volume-collected.json').write_text(json.dumps([{'date':'2000-01-03','turn':amount},{'date':'2026-11-02','turn':'17800'}]))
        values.append(publication_digest('volume',tmp_path,p))
    assert values[0]==values[1] and values[0]


CASES=json.loads((Path(__file__).parent/'fixtures/editorial_suite_cases.json').read_text(encoding='utf-8'))['cases']
@pytest.mark.parametrize('category',['volume','turnover','valuation','liquidity','oil_etf','us_barometer','us_structure','token_cost','gpu_rental'])
def test_sample_story_uses_real_prose_instructions_facts_and_survives_model_failure(category,monkeypatch):
    from app.briefs.editorial_authorization import adoption
    from app.briefs.skill_production import apply_skill_copy
    from app.briefs import gpu_editorial_ai, turnover_draft_ai
    contexts=[]
    def unavailable(settings,context):contexts.append(context);return {'status':'failed','reason':'quota','http_status':402}
    if category == 'turnover':
        monkeypatch.setattr(turnover_draft_ai, 'request_draft', unavailable)
    else:
        monkeypatch.setattr(gpu_editorial_ai, 'select_plan', unavailable)
    p=copy.deepcopy(next(c['payload'] for c in CASES if c['category']==category and c['expect']['status']=='ok'))
    original=copy.deepcopy(p);settings={'brief_editorial':adoption('test-operator')}
    settings['skills']={category:{'enabled':True,'instructions':'优先写核心变化，再给比较；不得编造任何事实。'}}
    apply_skill_copy(p,category,settings)
    assert p['editorial']['status']=='active',p['editorial']
    assert p['editorial']['writer']=='sample_story_v2' and p['facts']==original['facts']
    context, = contexts
    if category == 'turnover':
        assert {'date', 'low_turnover', 'high_turnover', 'reference_line'} <= context['facts'].keys()
        assert context['facts'].keys().isdisjoint(original['facts'].keys() - {'date'})
    else:
        assert context['facts'] == original['facts']
    assert context['instructions'].startswith('优先写核心变化')
    assert context['examples'] and all(e['body'] and e['title'] for e in context['examples'])
    assert p['editorial']['candidate']['style_quality']['human_acceptance']=='pending'
    assert p['skill']['status']=='failed' and p['dynamic_body']


@pytest.mark.parametrize('category,headline,fact,fact_text', [
    ('volume', '沪深两市成交额回升', 'volume', '沪深两市成交额上升。'),
    ('turnover', 'A股交易活跃度回升', 'turnover', '换手率上升。'),
])
def test_plan_story_merges_standalone_reading_into_previous_paragraph(category, headline, fact, fact_text):
    from app.briefs.sample_story import render_plan

    context = {
        'category': category,
        'printed_day': '11月6日',
        'headlines': [{'id': 'title.0', 'text': headline}],
        'fact_bank': [
            {'id': fact, 'variants': [{'id': fact + '.0', 'text': fact_text}]},
            {'id': 'reading', 'variants': [{'id': 'reading.0', 'text': '交易活跃度回升。'}]},
        ],
    }
    plan = {'headline': 'title.0', 'paragraphs': [[fact + '.0'], ['reading.0']]}

    assert render_plan(context, plan) == (headline, '11月6日，' + fact_text + '交易活跃度回升。')
    assert plan['paragraphs'] == [[fact + '.0'], ['reading.0']]


def test_old_adopted_pack_remains_effective_when_new_pack_changes(monkeypatch):
    from app.briefs.editorial_authorization import adoption,resolve_pack
    from app.briefs import editorial_suite
    settings={'brief_editorial':adoption('operator')};current=editorial_suite.load_pack('volume')
    changed=copy.deepcopy(current);changed['version']='999';changed['modules']['headlines'][0]['text']='新标题待采用'
    changed=editorial_suite._checked_pack('volume',changed)
    monkeypatch.setattr(editorial_suite,'load_pack',lambda category:changed)
    effective,pending=resolve_pack('volume',settings)
    assert pending=='999' and effective['digest']==current['digest']


def test_collection_does_not_render_or_replace_product_cache(tmp_path,monkeypatch):
    from app.briefs.runner import generate
    from app.briefs import runner
    monkeypatch.setenv('DATA_COLLECTION_ONLY','1')
    cache=tmp_path/'cache/us_barometer/2026-11-02.json';cache.parent.mkdir(parents=True)
    cache.write_text('{"original_product":"unchanged"}')
    before=cache.read_bytes()
    monkeypatch.setitem(runner.ADAPTERS,'us_barometer',lambda *a,**kw:payload())
    result=generate('us_barometer','2026-11-02',tmp_path,'collect-only',action='refresh')
    assert result['valid'] and result['collection_only'] and not result['images']
    assert cache.read_bytes()==before


def test_current_us_barometer_skips_unrelated_historical_repair(tmp_path,monkeypatch):
    from app.briefs import sources
    from app.briefs.engines.us_barometer import run
    from app.briefs.engines.us_structure import fetch_data
    from tests.test_us_barometer import fixtures
    day,wilshire,gdp,erp=fixtures();state=tmp_path/'history';state.mkdir()
    snapshot={'target':day.isoformat(),'wilshire':wilshire,'gdp':gdp,'erp':erp,'sources':{}}
    monkeypatch.setattr(run,'fetch',lambda *a,**kw:copy.deepcopy(snapshot))
    monkeypatch.setattr(run,'render',lambda snap,out,**kw:{**payload(),'data_date':day.isoformat()})
    monkeypatch.setattr(fetch_data,'repair',lambda *a,**kw:pytest.fail('old holes cannot block a ready current observation'))
    sources.us_barometer(day.isoformat(),tmp_path,state)


def test_oil_scheduler_rechecks_latest_in_slots_and_protects_edits(db_factory,seed):
    from app.db.models import BriefConfig
    from app.briefs.worker import schedule
    from app.briefs.policy import CHINA
    team=seed['editor'].team_id
    with db_factory() as db:
        db.add(BriefConfig(team_id=team,enabled=True))
        issue=ensure_issue(db,team,date(2026,11,3),'oil_etf')
        append_version(db,issue,payload());db.commit();issue_id=issue.id
    for minute in (5,6,22):
        schedule(db_factory,datetime(2026,11,3,8,minute,tzinfo=CHINA))
        with db_factory() as db:
            for j in db.scalars(select(BriefJob).where(BriefJob.category=='oil_etf')):j.status='succeeded';j.active_key=None
            db.commit()
    with db_factory() as db:
        jobs=list(db.scalars(select(BriefJob).where(BriefJob.category=='oil_etf')))
        assert len(jobs)==2 and all(j.schedule_origin for j in jobs)
        issue=db.get(type(issue),issue_id);append_version(db,issue,payload(),kind='edited',body='人工成果');db.commit()
    schedule(db_factory,datetime(2026,11,3,8,45,tzinfo=CHINA))
    with db_factory() as db:assert len(list(db.scalars(select(BriefJob).where(BriefJob.category=='oil_etf'))))==2


def test_date_presence_is_separate_from_business_field_quality(db_factory,seed,monkeypatch):
    from app.briefs import data_reliability as reliability
    monkeypatch.setitem(reliability.HORIZON,'volume',0)
    with db_factory() as db:
        reliability.save_points(db,seed['editor'].team_id,'volume',[{'date':'2026-11-02','series':'volume','method':'jin10_hushen_turnover_yi_v1','values':{}}])
        metric=reliability.coverage(db,seed['editor'].team_id,'volume','2026-11-02')['metrics']['volume']
        assert metric['date_coverage_complete'] and not metric['required_fields_valid'] and not metric['complete']


def test_unrelated_category_style_change_does_not_invalidate_volume(tmp_path,monkeypatch):
    from app.briefs import runner,skill_ai
    for category in ('volume','turnover'):
        root=tmp_path/category;(root/'references').mkdir(parents=True);(root/'production').mkdir()
        for name in ('SKILL.md','references/history.md','production/style-pack.json'):(root/name).write_text('same')
    actual=skill_ai.asset_dir
    monkeypatch.setattr(skill_ai,'asset_dir',lambda c:tmp_path/c if c in ('volume','turnover') else actual(c))
    runner.pipeline_signature.cache_clear()
    first={c:runner.pipeline_signature(c) for c in ('volume','turnover')}
    (tmp_path/'turnover/references/history.md').write_text('changed turnover only')
    runner.pipeline_signature.cache_clear()
    assert runner.pipeline_signature('volume')==first['volume']
    assert runner.pipeline_signature('turnover')!=first['turnover']
    runner.pipeline_signature.cache_clear()


def test_calendar_expiry_is_visible_and_unofficial_extension_is_rejected():
    from app.services.production_health import calendar_status
    from app.briefs.calendar_update import validate_bundle
    from app.briefs.trading_calendar import calendars
    assert next(r for r in calendar_status(date(2026,11,3)) if r['market']=='xshg')['status']=='needs_update'
    with pytest.raises(ValueError,match='官方'):
        validate_bundle({'market':'xshg','year':2027,'source_url':'https://untrusted.example/calendar'},b'',calendars())


def test_legacy_evidence_compaction_keeps_original_bytes_and_capture_time(db_factory,seed):
    from app.briefs.evidence_compaction import compact
    raw=b'original source';stamp=datetime(2026,10,30,tzinfo=timezone.utc)
    with db_factory() as db:
        j=BriefJob(team_id=seed['editor'].team_id,issue_date=date(2026,11,3),category='oil_etf',attempts=1,dedupe_key='legacy-evidence')
        db.add(j);db.flush()
        s=BriefSourceSnapshot(team_id=j.team_id,job_id=j.id,attempt=1,category=j.category,data_date=j.issue_date,
            relative_path='original.html',sha256=hashlib.sha256(raw).hexdigest(),byte_count=len(raw),content=raw,captured_at=stamp)
        db.add(s);db.commit();assert s.blob_id is None
        compact(db,j.team_id);db.commit();db.expire_all()
        assert s.content==raw and s._content==b'' and s.blob_id
        assert s.captured_at.replace(tzinfo=timezone.utc)==stamp
