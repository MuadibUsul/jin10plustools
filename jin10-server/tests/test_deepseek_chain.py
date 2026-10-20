import json
import os
from datetime import date
from unittest.mock import patch

from sqlalchemy import select

from app.briefs.engines.liquidity.copy_tracks import generate_deepseek
from app.briefs.runner import generate
from app.briefs.service import append_version, ensure_issue, issue_out
from app.briefs.sources import save
from app.db.models import BriefVersion, Team


def test_deepseek_request_and_diagnostics(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-secret-key')
    monkeypatch.setenv('DEEPSEEK_MODEL','deepseek-v4-pro')
    rule={'approved_titles':['标题'],'approved_bodies':['正文']}
    requests=[]
    class Reply:
        reason='stop'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return json.dumps({'choices':[{'finish_reason':self.reason,'message':{'content':json.dumps({'title':'标题','body':'正文'})}}], 'usage':{'completion_tokens':80}}).encode()
    reply=Reply()
    def open_request(request,timeout):
        requests.append(json.loads(request.data));assert timeout==60;return reply
    with patch('app.briefs.engines.liquidity.copy_tracks.urlopen',open_request):
        result=generate_deepseek(rule)
        assert result['status']=='ok' and result['model']=='deepseek-v4-pro'
        assert requests[0]['thinking']=={'type':'disabled'} and requests[0]['max_tokens']==4096
        reply.reason='length'
        failed=generate_deepseek(rule)
        assert failed['reason']=='output_truncated' and failed['finish_reason']=='length'
        assert failed['usage']['completion_tokens']==80
        assert 'test-secret-key' not in json.dumps(failed)


def test_parse_answer_accepts_fenced_json():
    from app.briefs.engines.liquidity.copy_tracks import parse_answer
    assert parse_answer('```json\n{"title":"标题","body":"正文"}\n```') == {'title': '标题', 'body': '正文'}


def test_chat2api_provider_uses_openai_compatible_payload(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER','chat2api')
    monkeypatch.setenv('AI_API_KEY','sk-c2a-test')
    monkeypatch.setenv('AI_MODEL','gpt-4o-mini')
    monkeypatch.setenv('AI_BASE_URL','http://chat2api:5005/c2a_test/v1')
    rule={'approved_titles':['标题'],'approved_bodies':['正文']}
    seen=[]
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):
            inner=json.dumps({'title':'标题','body':'正文'},ensure_ascii=False)
            return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'<escape>'+inner+'</escape>'}}]}).encode()
    def open_request(request,timeout):
        seen.append(request);return Reply()
    with patch('app.briefs.engines.liquidity.copy_tracks.urlopen',open_request):
        result=generate_deepseek(rule)
    assert result['status']=='ok' and result['provider']=='chat2api' and result['model']=='gpt-4o-mini'
    assert seen[0].full_url=='http://chat2api:5005/c2a_test/v1/chat/completions'
    payload=json.loads(seen[0].data)
    assert 'thinking' not in payload and payload['response_format']=={'type':'json_object'}


def test_cached_data_can_complete_ai_without_refetch(tmp_path,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    monkeypatch.delenv('DEEPSEEK_MODEL',raising=False)
    def source(day,out,state,refresh=False):
        (out/'image.png').write_bytes(b'original-image')
        save(out/'plot-data.json',[{'date':day}])
        return {'title':'【测试】标题','dynamic_body':'正文','data_date':day,'images':['image.png'],'facts':{},'warnings':[]}
    with patch.dict('app.briefs.runner.ADAPTERS',{'volume':source}):
        first=generate('volume','2026-10-15',tmp_path,'first')
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-key')
    monkeypatch.setenv('DEEPSEEK_MODEL','deepseek-v4-pro')
    with patch.dict('app.briefs.runner.ADAPTERS',{'volume':lambda *args,**kw:(_ for _ in ()).throw(AssertionError('unexpected fetch'))}),patch('app.briefs.engines.liquidity.copy_tracks.generate_deepseek',return_value={'status':'ok','model':'deepseek-v4-pro','title':first['title'],'body':first['dynamic_body']}) as ai:
        second=generate('volume','2026-10-15',tmp_path,'second')
        assert second['deepseek']['status']=='ok' and ai.call_count==1
        assert (tmp_path/second['artifact_dir']/'image.png').read_bytes()==b'original-image'
        third=generate('volume','2026-10-15',tmp_path,'third')
        assert third['deepseek']['status']=='ok' and ai.call_count==1


def test_only_two_deliverable_versions_and_latest_visible(db_factory,seed):
    with db_factory() as db:
        issue=ensure_issue(db,seed['editor'].team_id,date(2026,10,16),'volume')
        for i in range(4):
            append_version(db,issue,{'title':f'标题{i}','dynamic_body':'正文','fixed_notes':f'注释{i}','images':[],'valid':True})
        db.commit()
        versions=list(db.scalars(select(BriefVersion).where(BriefVersion.issue_id==issue.id).order_by(BriefVersion.number.desc())))
        assert [v.number for v in versions]==[4,3]
        assert [v['number'] for v in issue_out(db,issue)['versions']]==[4,3]
        archived=db.get(Team,issue.team_id).settings['retired_issue_notes']
        assert [v['notes'] for v in archived]==['注释0','注释1']
