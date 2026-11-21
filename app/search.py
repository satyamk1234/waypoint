import math
from collections import Counter
import numpy as np
from sqlalchemy import select, cast, or_, and_
from pgvector.sqlalchemy import Vector
from app.db import Job, now
from datetime import timedelta
from app.locations import country, ALIASES
from urllib.parse import urlsplit, parse_qsl, urlencode
import time
from app.embeddings import tokens, embed, model_id
from app.schemas import Preferences


def job_dict(job):
    return {k: getattr(job,k) for k in ('id','source','title','company','location','remote','employment_type','description','url','skills','salary','source_url','location_scope','countries','timezones','published_at','expires_at')}

def document(job):
    return f'{job.title} {job.title} {job.company} {" ".join(job.skills)} {job.description}'

def visible_jobs(stmt):
    # Hide known expirations immediately, even between refreshes. Hide stale live data.
    return stmt.where(Job.active.is_(True), or_(Job.expires_at.is_(None), Job.expires_at > int(time.time())),
        or_(Job.source=='sample', Job.seen_at >= now()-timedelta(hours=48)))

def constraints(stmt, prefs):
    if prefs.real_only:
        stmt=stmt.where(Job.source!='sample')
    if prefs.remote is not None:
        stmt = stmt.where(Job.remote == prefs.remote)
    requested_country=prefs.country
    location=prefs.location
    # Preserve existing conversations that saved a country in the old location field.
    if location and location.strip().casefold() in ALIASES:
        requested_country=requested_country or location
        location=None
    if requested_country:
        token=country(requested_country).replace('|','')
        escaped=token.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        stmt=stmt.where(or_(Job.location_scope=='worldwide',
            and_(Job.location_scope=='countries',Job.country_tokens.like(f'%|{escaped}|%',escape='\\'))))
    if location:
        escaped=location.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        stmt=stmt.where(Job.location.ilike(f'%{escaped}%',escape='\\'))
    if prefs.employment_type:
        stmt=stmt.where(Job.employment_type==prefs.employment_type)
    return stmt


def reasons(job, prefs, query=''):
    out = []
    overlap = sorted(set(tokens(query + ' ' + ' '.join(prefs.skills))) & set(tokens(job.title+' '+' '.join(job.skills))))
    if overlap:
        out.append('Relevant terms: ' + ', '.join(overlap[:6]))
    if prefs.remote is not None:
        out.append('Remote arrangement matches' if job.remote else 'On-site arrangement matches')
    if prefs.location or prefs.country:
        out.append('Listed eligibility: ' + job.location)
    if job.location_scope=='unknown':
        out.append('Country eligibility is not specified')
    if job.timezones:
        out.append('Check the listed timezone restrictions')
    if prefs.employment_type:
        out.append('Employment type matches: ' + job.employment_type)
    return out or ['Retrieved from the job description; review requirements before applying.']

def search(db, query, prefs=None, limit=6, mode='rerank'):
    prefs = prefs or Preferences()
    stmt = constraints(visible_jobs(select(Job)).where(Job.embedding_model == model_id()), prefs)
    jobs = list(db.scalars(stmt))
    if not jobs:
        return []
    # Exact BM25 over the filtered corpus; deliberately simple for this MVP.
    terms = tokens(query + ' ' + (prefs.role or '') + ' ' + ' '.join(prefs.skills))
    docs = [Counter(tokens(document(j))) for j in jobs]
    lengths = [sum(d.values()) for d in docs]
    avg = sum(lengths)/len(lengths) or 1
    df = Counter(t for d in docs for t in d)
    scores = []
    for d, length in zip(docs, lengths):
        score = sum(math.log(1+(len(jobs)-df[t]+.5)/(df[t]+.5)) * d[t]*2.5/(d[t]+1.5*(.25+.75*length/avg)) for t in terms if d[t])
        scores.append(score)
    bm = sorted([(j.id,s) for j,s in zip(jobs,scores) if s>0], key=lambda p:(-p[1],p[0]))[:60]
    vector = []
    if mode != 'bm25':
        q = embed([query + ' ' + (prefs.role or '') + ' ' + ' '.join(prefs.skills)])[0]
        if db.bind.dialect.name == 'postgresql':
            distance = cast(Job.embedding, Vector(384)).cosine_distance(q)
            ordered = db.scalars(stmt.order_by(distance).limit(60)).all()
            vector = [(j.id, float(np.dot(j.embedding,q))) for j in ordered]
        else:
            vector = sorted([(j.id,float(np.dot(j.embedding,q))) for j in jobs],key=lambda p:(-p[1],p[0]))[:60]
    if mode == 'bm25':
        ranked = bm
    elif mode == 'vector':
        ranked = vector
    else:
        fused = Counter()
        for ranking in [bm,vector]:
            for rank,(jid,_) in enumerate(ranking,1):
                fused[jid] += 1/(60+rank)
        by_id = {j.id:j for j in jobs}
        if mode == 'rerank':
            for jid in fused:
                j=by_id[jid]
                title_overlap = len(set(terms)&set(tokens(j.title)))/max(len(set(terms)),1)
                skill_overlap = len(set(s.lower() for s in prefs.skills)&set(s.lower() for s in j.skills))/max(len(prefs.skills),1)
                fused[jid] += .012*title_overlap + .008*skill_overlap
        ranked = sorted(fused.items(), key=lambda p:(-p[1],p[0]))
    lookup = {j.id:j for j in jobs}
    result=[]; seen=set()
    for jid,score in ranked:
        job=lookup[jid]
        url=urlsplit(job.url)
        params=urlencode(sorted((k,v) for k,v in parse_qsl(url.query) if not k.lower().startswith('utm_')))
        key=(url.netloc.lower(),url.path.rstrip('/'),params)
        if key in seen: continue
        seen.add(key)
        result.append(dict(job_dict(job),score=round(score,5),reasons=reasons(job,prefs,query)))
        if len(result)==limit: break
    return result
