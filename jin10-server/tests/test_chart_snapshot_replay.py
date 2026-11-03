import copy,hashlib,json,math,shutil
from datetime import date,timedelta
from pathlib import Path

import matplotlib
from app.briefs.charts import valuation_chart
from app.briefs.runner import rerender
from app.briefs.trading_calendar import sessions


def test_valuation_pixels_do_not_depend_on_prior_plot_global_state(tmp_path):
    days=[(date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(500)]
    buffett=[{'date':day,'marketCap':str(80+math.sin(i/7)*4),'gdp':'100',
        'close':str(4000+math.cos(i/3)*800)} for i,day in enumerate(days)]
    erp=[{'date':day,'erp_percent':str(5+math.sin(i/9)),'close':row['close']} for i,(day,row) in enumerate(zip(days,buffett))]
    original=copy.deepcopy((buffett,erp));hashes=[]
    for label,simplify in (('default',True),('previous_erp_plot',False)):
        out=tmp_path/label;out.mkdir()
        with matplotlib.rc_context({'path.simplify':simplify,'agg.path.chunksize':400}):
            valuation_chart(buffett,erp,out)
            assert matplotlib.rcParams['path.simplify'] is simplify
            assert matplotlib.rcParams['agg.path.chunksize']==400
        hashes.append(hashlib.sha256((out/'股债性价比.png').read_bytes()).hexdigest())
    assert hashes[0]==hashes[1]
    assert (buffett,erp)==original


def test_turnover_redraw_uses_the_archived_calendar_and_keeps_missing_window_empty(tmp_path):
    from app.briefs.engines.turnover import build_turnover
    wanted=sessions('xshg','2026-08-03','2026-10-30')
    missing=wanted[15]
    rows=[{'date':day,'totalCompany':100,'belowPercent3':50+i%9,'belowPercent5':80,
        'close':str(3000+i)} for i,day in enumerate(wanted) if day!=missing]
    source=tmp_path/'source';source.mkdir();out=tmp_path/'redraw';out.mkdir()
    (source/'source.json').write_text(json.dumps(rows),encoding='utf-8')
    (source/'turnover-calendar.json').write_text(json.dumps({'sessions':wanted}),encoding='utf-8')
    shutil.copy2(source/'source.json',out/'source.json')
    # The standalone engine has sibling imports, as in the production process.
    import sys
    engine=str(Path(build_turnover.__file__).parent)
    sys.path.insert(0,engine)
    try:
        initial=build_turnover.build_outputs(source/'source.json',source,start='2026-08-03',as_of=wanted[-1],session_dates=wanted)
        previous={'images':[Path(initial['png']).name],'title':'已校验标题','dynamic_body':'已校验正文'}
        result=rerender('turnover',previous,source,out,wanted[-1])
    finally:sys.path.remove(engine)
    first=json.loads((source/'verification.json').read_text(encoding='utf-8'))
    second=json.loads((out/'verification.json').read_text(encoding='utf-8'))
    assert first['latest']==second['latest'] and first['display_points']==second['display_points']
    assert first['display_points']<len(rows)-8
    assert hashlib.sha256(Path(initial['png']).read_bytes()).hexdigest()==hashlib.sha256((out/result['images'][0]).read_bytes()).hexdigest()


def test_render_binds_the_current_edition_and_preserves_style_without_replacing_date_cache(tmp_path,monkeypatch):
    from app.briefs.runner import generate
    days=sessions('xshg','2026-09-01','2026-11-02')[-25:]
    source=tmp_path/'runs'/'selected';source.mkdir(parents=True)
    rows=[{'date':day,'turn':13000+i,'vol':100,'ma20':12500+i} for i,day in enumerate(days)]
    (source/'plot-data.json').write_text(json.dumps(rows),encoding='utf-8')
    selected={'data_date':days[-1],'title':'编辑采用的风格标题','dynamic_body':'编辑采用的完整正文',
        'facts':{'source':'已核验事实'},'fixed_notes':'已保存的注释','pipeline':'original-rules',
        'editorial':{'status':'active'},'skill':{'status':'ok'},'rule_copy':{'body':'旧规则'},
        'artifact_dir':'runs/selected','images':['missing-original.png'],'image_sha256':{}}
    run=tmp_path/'runs'/'redraw-edition';run.mkdir()
    selected_path=run/'render-input.json';selected_path.write_text(json.dumps(selected),encoding='utf-8')
    cache=tmp_path/'cache'/'volume'/(days[-1]+'.json');cache.parent.mkdir(parents=True)
    cache.write_text('invalid date cache must not affect the selected edition',encoding='utf-8')
    before=cache.read_bytes()
    def forbidden(*a,**kw):raise AssertionError('A redraw cannot fetch new data')
    monkeypatch.setattr('app.briefs.sources.urlopen',forbidden)
    result=generate('volume',days[-1],tmp_path,'redraw-edition',action='render',render_input=selected_path)
    for key in ('title','dynamic_body','facts','fixed_notes','editorial','skill','rule_copy','pipeline'):
        assert result[key]==selected[key]
    assert result['images']==['成交额.png'] and result['valid']
    assert cache.read_bytes()==before


def test_render_rejects_a_payload_outside_its_own_run(tmp_path):
    import pytest
    from app.briefs.runner import generate
    outside=tmp_path/'outside.json';outside.write_text('{}')
    with pytest.raises(ValueError,match='属于本次任务'):
        generate('volume','2026-11-02',tmp_path,'new-run',action='render',render_input=outside)


def test_image_only_run_cannot_revert_observations_or_latest_source_check(db_factory,seed,tmp_path):
    from sqlalchemy import select
    from app.db.models import BriefJob,BriefDataPoint,BriefDataCheck
    from app.briefs.data_reliability import save_points,record_run
    with db_factory() as db:
        team=seed['editor'].team_id
        save_points(db,team,'volume',[{'series':'volume','date':'2026-11-02',
            'method':'jin10_hushen_turnover_yi_v1','values':{'date':'2026-11-02','turn':'14000'}}])
        (tmp_path/'plot-data.json').write_text(json.dumps([{'date':'2026-11-02','turn':'13000'}]))
        job=BriefJob(team_id=team,issue_date=date(2026,11,3),category='volume',action='render',
            dedupe_key='image-only-check',attempts=1)
        db.add(job);db.flush()
        report=record_run(db,job,tmp_path,{'valid':True,'data_date':'2026-11-02',
            'data_reliability':{'publication_digest':'original-edition'}})
        assert report=={'publication_digest':'original-edition'}
        points=list(db.scalars(select(BriefDataPoint)))
        assert len(points)==1 and points[0].values['turn']=='14000'
        assert list(db.scalars(select(BriefDataCheck)))==[]


def test_recovery_does_not_treat_an_old_image_replay_as_a_new_acquisition(db_factory,seed,tmp_path):
    from sqlalchemy import select
    from app.db.models import BriefJob,BriefDataPoint
    from app.briefs.source_archive import archive_run
    from app.briefs.repair import recover_snapshots
    with db_factory() as db:
        team=seed['editor'].team_id
        for action,amount in (('produce','14000'),('render','13000')):
            folder=tmp_path/action;folder.mkdir()
            (folder/'plot-data.json').write_text(json.dumps([{'date':'2026-11-02','turn':amount}]))
            job=BriefJob(team_id=team,category='volume',issue_date=date(2026,11,3),action=action,
                dedupe_key='recover-'+action,attempts=1)
            db.add(job);db.flush();archive_run(db,job,folder,data_date='2026-11-02')
        assert recover_snapshots(db,team,'volume',tmp_path/'recovered',replay_revisions=True)==1
        points=list(db.scalars(select(BriefDataPoint)))
        assert len(points)==1 and points[0].values['turn']=='14000'
