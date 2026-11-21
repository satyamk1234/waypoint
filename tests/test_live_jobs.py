import copy
import json
import time
from datetime import timedelta
import httpx
import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import sessionmaker
from app.db import Job, SourceRefresh, migrate_job_metadata, now
from app.schemas import Preferences
from app.search import search
from app.ingest import ingest
from app.sources import himalayas, parse_himalayas
from app.agent import update_preferences
from app.schemas import PreferencePatch


def posting(slug='python',**overrides):
    return dict(guid='https://himalayas.app/companies/acme/jobs/'+slug,
        applicationLink='https://himalayas.app/companies/acme/jobs/'+slug+'?utm_source=api',
        title='Python Engineer',companyName='Acme',description='<p>Build Python APIs with SQL.</p>',
        employmentType='Full Time',locationRestrictions=['India'],timezoneRestrictions=[5.5],
        pubDate=int(time.time())-3600,expiryDate=int(time.time())+86400,**overrides)


def test_provider_metadata_and_required_application_link():
    raw=posting(); item=parse_himalayas(raw)
    assert item['url']==raw['applicationLink']
    assert item['source_url']==raw['guid']
    assert item['employment_type']=='full_time'
    assert item['countries']==['India'] and item['timezones']==[5.5]
    assert 'Python' in item['skills'] and '<p>' not in item['description']
    assert len(item['id'])<=120


def test_missing_country_is_unknown_explicit_empty_is_worldwide():
    raw=posting(); del raw['locationRestrictions']
    assert parse_himalayas(raw)['location_scope']=='unknown'
    raw['locationRestrictions']=[]
    assert parse_himalayas(raw)['location_scope']=='worldwide'
    raw['locationRestrictions']='India'
    with pytest.raises(ValueError): parse_himalayas(raw)


def test_cursor_pagination_and_partial_snapshot():
    seen=[]
    def serve(request):
        seen.append(dict(request.url.params))
        if request.url.params.get('cursor')=='next':
            return httpx.Response(200,json={'jobs':[posting('two')],'totalCount':2})
        return httpx.Response(200,json={'jobs':[posting()],'totalCount':2,'nextCursor':'next'})
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        rows,complete=himalayas(2,client,pause=0)
        assert len(rows)==2 and complete and seen[1]['cursor']=='next'
        rows,complete=himalayas(1,client,pause=0)
        assert len(rows)==1 and not complete


def test_repeated_cursor_and_http_errors_fail_batch():
    for response in [httpx.Response(200,json={'jobs':[posting()],'totalCount':3,'nextCursor':'loop'}),httpx.Response(429)]:
        with httpx.Client(transport=httpx.MockTransport(lambda request:response)) as client:
            with pytest.raises((ValueError,httpx.HTTPStatusError)): himalayas(3,client,pause=0)


def test_missing_cursor_with_large_total_is_not_complete():
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'jobs':[posting()],'totalCount':100}))) as client:
        assert himalayas(2,client,pause=0)[1] is False


def test_real_default_country_and_expiry_filters(populated):
    india=parse_himalayas(posting('india'))
    worldwide=copy.deepcopy(india); worldwide.update(id='world',countries=[],location_scope='worldwide',location='Worldwide',url='https://example.com/world')
    us=copy.deepcopy(india); us.update(id='us',countries=['United States'],location='United States',url='https://example.com/us')
    unknown=copy.deepcopy(india); unknown.update(id='unknown',countries=[],location_scope='unknown',location='Not specified',url='https://example.com/unknown')
    expired=copy.deepcopy(india); expired.update(id='expired',expires_at=int(time.time())-1,url='https://example.com/expired')
    ingest(populated,[india,worldwide,us,unknown,expired])
    for mode in ['bm25','vector','hybrid','rerank']:
        ids={j['id'] for j in search(populated,'Python',Preferences(country='IN'),limit=20,mode=mode)}
        assert ids=={india['id'],'world'}
    ids={j['id'] for j in search(populated,'Python',Preferences(location='India'),limit=20)}
    assert ids=={india['id'],'world'}
    assert all(j['source']!='sample' for j in search(populated,'Python',limit=20))
    assert any(j['source']=='sample' for j in search(populated,'Python',Preferences(real_only=False),limit=20))


def test_expiry_and_staleness_checked_at_search_time(db):
    item=parse_himalayas(posting()); ingest(db,[item])
    row=db.get(Job,item['id']); row.expires_at=int(time.time())-1; row.active=True; db.commit()
    assert search(db,'Python')==[]
    row.expires_at=None; row.seen_at=now()-timedelta(hours=49); db.commit()
    assert search(db,'Python')==[]


def test_tracking_variants_deduplicated_in_results(db):
    first=parse_himalayas(posting()); second=copy.deepcopy(first)
    second.update(id='another',url=first['source_url']+'?utm_source=other')
    ingest(db,[first,second]); assert len(search(db,'Python'))==1


def test_additive_migration_keeps_existing_rows_and_is_repeatable(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'legacy.db'))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE jobs (id TEXT PRIMARY KEY, location TEXT)'))
        conn.execute(text("INSERT INTO jobs VALUES ('kept', 'Bengaluru, India')"))
        conn.execute(text('CREATE TABLE preferences (user_id TEXT PRIMARY KEY, values_json TEXT)'))
        conn.execute(text("INSERT INTO preferences VALUES ('user', 'keep this')"))
    migrate_job_metadata(engine); migrate_job_metadata(engine)
    with engine.connect() as conn:
        row=conn.execute(text('SELECT id, location_scope, country_tokens FROM jobs')).one()
        assert tuple(row)==('kept','countries','|india|')
        assert conn.execute(text('SELECT values_json FROM preferences')).scalar()=='keep this'


def test_omitted_real_only_is_not_reset_by_agent_patch(db):
    update_preferences(db,'a'*32,PreferencePatch(real_only=True,country='India'))
    result=update_preferences(db,'a'*32,PreferencePatch(employment_type='full_time'))
    assert result.real_only and result.country=='India'


def test_daily_refresh_failure_preserves_data_and_partial_does_not_retire(db,tmp_path,monkeypatch):
    from app import refresh
    old=parse_himalayas(posting('old')); new=parse_himalayas(posting('new'))
    ingest(db,[old])
    monkeypatch.setattr(refresh,'LOCK_PATH',tmp_path/'refresh.lock')
    monkeypatch.setattr(refresh,'Session',sessionmaker(bind=db.bind,expire_on_commit=False))
    def failed_remotive(): raise RuntimeError('secret must never appear in stored errors')
    monkeypatch.setattr(refresh,'remotive',failed_remotive)
    monkeypatch.setattr(refresh,'himalayas',lambda pages:([new],False))
    result=refresh.refresh_sources()
    assert result['remotive']=={'status':'error','error':'RuntimeError'}
    assert result['himalayas']['complete_snapshot'] is False
    db.expire_all()
    assert db.get(Job,old['id']).active
    assert db.get(Job,new['id']).active
    assert refresh.refresh_sources()=={'remotive':{'status':'not_due'},'himalayas':{'status':'not_due'}}
    assert 'secret' not in json.dumps(db.get(SourceRefresh,'remotive').detail)
