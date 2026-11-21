"""Idempotent import. Run: python -m app.ingest --source sample|remotive|json."""
import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
import httpx
from sqlalchemy import select
from app.db import Job, Session, init_db, now
from app.embeddings import embed, model_id
from app.schemas import JobInput
from app.locations import listed_eligibility, country_tokens
import time

class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self, data):
        self.parts.append(data)

def clean(value):
    parser=PlainText(); parser.feed(value or '')
    return ' '.join(' '.join(parser.parts).split())

def ingest(db, rows, deactivate_source=None):
    # Validate the entire batch before writing or retiring any records.
    records=[JobInput.model_validate(row).model_dump(mode='json') for row in rows]
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Duplicate IDs in import')
    if deactivate_source and (not records or any(r['source']!=deactivate_source for r in records)):
        raise ValueError('Retirement requires a nonempty full snapshot of one source')
    changed=[]; unchanged=0; timestamp=now()
    for record in records:
        record['description']=clean(record['description'])
        if record['location_scope']=='unknown':
            record['location_scope'],record['countries']=listed_eligibility(record['location'])
        record['source_url']=record['source_url'] or record['url']
        digest=hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
        old=db.get(Job,record['id'])
        if old and old.content_hash==digest and old.embedding_model==model_id():
            old.seen_at=timestamp; old.active=not record['expires_at'] or record['expires_at']>int(time.time()); unchanged+=1
        else:
            changed.append((record,digest,old))
    vectors=embed([f"{r['title']} {' '.join(r['skills'])} {r['description']}" for r,_,_ in changed]) if changed else []
    for (record,digest,old),vector in zip(changed,vectors):
        obj=old or Job(id=record['id'])
        for key,value in record.items(): setattr(obj,key,value)
        obj.content_hash=digest; obj.embedding=vector; obj.embedding_model=model_id()
        obj.country_tokens=country_tokens(record['countries'])
        obj.updated_at=timestamp; obj.seen_at=timestamp
        obj.active=not record['expires_at'] or record['expires_at']>int(time.time())
        db.add(obj)
    retired=0
    if deactivate_source:
        ids={r['id'] for r in records}
        for job in db.scalars(select(Job).where(Job.source==deactivate_source,Job.active.is_(True))):
            if job.id not in ids: job.active=False; retired+=1
    db.commit()
    return {'embedded':len(changed),'unchanged':unchanged,'retired':retired}

def remotive():
    response=httpx.get('https://remotive.com/api/remote-jobs',timeout=60,headers={'User-Agent':'JobMatchingAgent/1.0'})
    response.raise_for_status()
    jobs=response.json()['jobs']
    if not jobs: raise ValueError('Empty feed; refusing snapshot retirement')
    return [dict(id=f"remotive-{j['id']}",source='remotive',title=j['title'],company=j['company_name'],location=j.get('candidate_required_location') or 'Not specified',remote=True,employment_type=j.get('job_type') or 'unspecified',description=clean(j['description'])[:30000],url=j['url'],skills=j.get('tags',[])[:50],salary=j.get('salary') or 'Not specified') for j in jobs]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',choices=['sample','remotive','himalayas','json'],default='sample')
    parser.add_argument('--file',type=Path)
    parser.add_argument('--max-pages',type=int,default=10)
    parser.add_argument('--retire-missing',action='store_true',help='Only for a complete Remotive snapshot')
    args=parser.parse_args(); init_db()
    complete=True
    if args.source=='himalayas':
        from app.sources import himalayas
        rows,complete=himalayas(max_pages=args.max_pages)
        if args.retire_missing and not complete: parser.error('Partial feed: refusing missing-record retirement')
    elif args.source=='remotive': rows=remotive()
    else:
        path=args.file if args.source=='json' else Path(__file__).resolve().parent.parent/'data/sample_jobs.json'
        if not path: parser.error('--file is required for JSON imports')
        rows=json.loads(path.read_text())
    with Session() as db:
        print(json.dumps(ingest(db,rows,args.source if args.source in ('remotive','himalayas') and args.retire_missing else None)))

if __name__=='__main__': main()
