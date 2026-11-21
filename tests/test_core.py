import copy
import json
import httpx
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.ingest import ingest
from app.search import search
from app.schemas import Preferences, PreferencePatch
from app.db import Job, Message, get_db
from app.agent import run_agent, AgentError, GroqClient, get_preferences, update_preferences
from app.evaluate import metrics
from app.main import app

UID='a'*32

def test_incremental_ingest_does_not_reembed_unchanged(populated,jobs):
    with patch('app.ingest.embed',side_effect=AssertionError('Must not embed unchanged rows')):
        assert ingest(populated,jobs)=={'embedded':0,'unchanged':30,'retired':0}
    changed=copy.deepcopy(jobs); changed[0]['description']+=' PostgreSQL'
    assert ingest(populated,changed)['embedded']==1
    assert populated.get(Job,changed[0]['id']).description.endswith('PostgreSQL')

def test_retirement_and_reactivation(populated,jobs):
    assert ingest(populated,jobs[:-1],deactivate_source='sample')['retired']==1
    assert not populated.get(Job,jobs[-1]['id']).active
    assert ingest(populated,jobs)['unchanged']==30
    assert populated.get(Job,jobs[-1]['id']).active
    with pytest.raises(ValueError): ingest(populated,[],deactivate_source='sample')

def test_validate_batch_before_changes(populated,jobs):
    bad=copy.deepcopy(jobs); bad[0]['url']='javascript:alert(1)'
    with pytest.raises(ValueError): ingest(populated,bad)
    assert len(populated.scalars(select(Job)).all())==30

def test_strict_filters_and_location_not_worldwide(populated):
    prefs=Preferences(real_only=False,remote=True,location='India',employment_type='internship')
    for mode in ['bm25','vector','hybrid','rerank']:
        results=search(populated,'Python',prefs,limit=20,mode=mode)
        assert results
        assert all(j['remote'] and 'India' in j['location'] and j['employment_type']=='internship' for j in results)
    assert search(populated,'Python',Preferences(location='Mars'))==[]
    assert search(populated,'Python',Preferences(location='%'))==[]

def test_relevant_role_ranks_first(populated):
    for mode in ['bm25','vector','hybrid','rerank']:
        assert search(populated,'React TypeScript frontend',Preferences(real_only=False),mode=mode)[0]['id'].startswith('sample-frontend')

def test_preference_clear_and_session_isolation(db):
    update_preferences(db,UID,PreferencePatch(role='Python',remote=True,skills=['Python']))
    update_preferences(db,UID,PreferencePatch(remote=None))
    p=get_preferences(db,UID)
    assert p.remote is None and p.role=='Python'
    assert get_preferences(db,'b'*32).role is None

def call(name,args,number=1):
    return {'id':f'call_{number}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}

class FakeGroq:
    def __init__(self,replies): self.replies=iter(replies); self.messages=[]
    def complete(self,messages):
        self.messages.append(list(messages))
        return next(self.replies)

def test_tool_loop_updates_then_searches_and_saves_memory(populated):
    client=FakeGroq([
        {'tool_calls':[call('update_preferences',{'remote':True,'location':'India','employment_type':'internship'})]},
        {'tool_calls':[call('search_jobs',{'query':'Python backend'})]},
        {'content':json.dumps({'message':'Here is a matching sample role.','job_ids':['sample-python-1']})}
    ])
    response=run_agent(populated,UID,'Remote Python internships in India',client)
    assert response['jobs'][0]['id']=='sample-python-1'
    assert get_preferences(populated,UID).remote is True
    assert len(populated.scalars(select(Message)).all())==2
    assert response['validation']=={'attempts':1,'valid':1,'invalid':0}

def test_rejects_fabricated_ids_and_recovers(populated):
    client=FakeGroq([{'content':'{"message":"Fake job", "job_ids":["fake"]}'},{'content':'{"message":"Please search first", "job_ids":[]}'}])
    result=run_agent(populated,UID,'Find a job',client)
    assert result['jobs']==[] and result['validation']['invalid']==1

def test_unknown_tools_never_execute(populated):
    client=FakeGroq([{'tool_calls':[call('delete_database',{})]},{'content':'{"message":"I can help search jobs", "job_ids":[]}'}])
    response=run_agent(populated,UID,'hi',client)
    assert response['trace']==[{'tool':'delete_database','ok':False}]
    assert populated.get(Job,'sample-python-1')

def test_groq_key_missing_and_rate_limit(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(),'groq_api_key','')
    with pytest.raises(AgentError,match='Add GROQ_API_KEY'): GroqClient().complete([])
    monkeypatch.setattr(settings(),'groq_api_key','fake-test-key')
    transport=httpx.MockTransport(lambda request:httpx.Response(429,json={'error':{}}))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(AgentError) as err: GroqClient(client).complete([])
        assert err.value.status==429

def test_http_session_search_and_delete(populated):
    app.dependency_overrides[get_db]=lambda:populated
    try:
        with TestClient(app) as client:
            assert client.post('/api/search',json={'query':'Python'}).status_code==401
            sid=client.post('/api/sessions').json()['session_id']
            headers={'X-Session-ID':sid}
            assert client.patch('/api/preferences',headers=headers,json={'remote':True,'real_only':False}).status_code==200
            response=client.post('/api/search',headers=headers,json={'query':'Python'})
            assert response.status_code==200 and response.json()['jobs']
            assert client.post('/api/chat',headers=headers,json={'message':'hello'}).status_code==503
            assert client.post('/api/search',headers=headers,json={'query':'x','limit':999}).status_code==422
            assert client.delete('/api/session',headers=headers).status_code==204
            assert client.get('/api/preferences',headers=headers).status_code==401
    finally: app.dependency_overrides.clear()

def test_metrics():
    assert metrics(['a','b'],{'a':2,'b':1},2)=={'recall':1,'ndcg':1}
    assert metrics(['z'],{'a':2},1)=={'recall':0,'ndcg':0}
