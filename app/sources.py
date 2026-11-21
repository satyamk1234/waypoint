"""Himalayas public feed adapter; bounded cursor pagination and no key required."""
import hashlib
import re
import time
from urllib.parse import urlsplit
import httpx
from app.config import settings
from app.ingest import clean
from app.schemas import JobInput

SKILLS=['Python','SQL','JavaScript','TypeScript','React','Java','Docker','Kubernetes','AWS','FastAPI','Django','PyTorch','TensorFlow','Go','Rust','C++','PostgreSQL','Excel','Figma']

def parse_himalayas(job):
    guid=job['guid']
    parsed=urlsplit(guid)
    if parsed.scheme!='https' or parsed.netloc!='himalayas.app' or not parsed.path.startswith('/companies/'):
        raise ValueError('Unexpected Himalayas job identifier')
    # Missing restrictions mean unknown; only an explicit empty list means worldwide.
    countries=job.get('locationRestrictions')
    if countries is not None and (not isinstance(countries,list) or not all(isinstance(c,str) for c in countries)):
        raise ValueError('Invalid country restrictions')
    scope='unknown' if countries is None else ('countries' if countries else 'worldwide')
    location=', '.join(countries) if countries else ('Worldwide' if scope=='worldwide' else 'Not specified')
    description=clean(job.get('description') or job.get('excerpt') or '')
    employment=re.sub(r'[\s-]+','_',job.get('employmentType','unspecified').strip().lower())
    employment={'fulltime':'full_time','parttime':'part_time','intern':'internship','contractor':'contract'}.get(employment,employment)
    amounts=[str(job[k]) for k in ['minSalary','maxSalary'] if job.get(k) is not None]
    salary=(' – '.join(amounts)+' '+(job.get('currency') or '')+' / '+(job.get('salaryPeriod') or 'annual')).strip() if amounts else 'Not specified'
    return JobInput.model_validate(dict(
        id='himalayas-'+hashlib.sha256(guid.encode()).hexdigest()[:32],source='himalayas',
        title=job['title'],company=job['companyName'],location=location[:300],remote=True,
        employment_type=employment,description=description[:30000],url=job['applicationLink'],source_url=guid,
        skills=[s for s in SKILLS if re.search(r'(?<!\w)'+re.escape(s)+r'(?!\w)',description,re.I)],salary=salary,
        location_scope=scope,countries=countries or [],timezones=job.get('timezoneRestrictions') or [],
        published_at=job.get('pubDate'),expires_at=job.get('expiryDate'),
    )).model_dump(mode='json')

def himalayas(max_pages=10, client=None, pause=1.1):
    if not 1<=max_pages<=100: raise ValueError('max_pages must be 1–100')
    headers={'User-Agent':'WaypointJobMatching/1.1'}
    if settings().himalayas_api_key: headers['Authorization']='Bearer '+settings().himalayas_api_key
    owned=client is None
    client=client or httpx.Client(timeout=45)
    rows={}; cursor=None; cursors=set(); complete=False
    try:
        for page in range(max_pages):
            params={'limit':20}
            if cursor: params['cursor']=cursor
            response=client.get('https://himalayas.app/jobs/api',params=params,headers=headers)
            response.raise_for_status()
            data=response.json()
            if not isinstance(data.get('jobs'),list): raise ValueError('Missing job array')
            for job in data['jobs']:
                record=parse_himalayas(job); rows[record['id']]=record
            cursor=data.get('nextCursor')
            if not cursor:
                # Never call a truncated/ambiguous response a complete snapshot.
                total=data.get('totalCount')
                complete=isinstance(total,int) and len(rows)>=total
                break
            if not isinstance(cursor,str) or cursor in cursors:
                raise ValueError('Repeated/invalid pagination cursor')
            cursors.add(cursor)
            if page+1<max_pages and pause: time.sleep(pause)
    finally:
        if owned: client.close()
    if not rows: raise ValueError('Empty feed; keeping previous index')
    return list(rows.values()),complete
