"""Daily refresh while the app runs; `python -m app.refresh` also works standalone."""
import argparse
import asyncio
import fcntl
import json
import logging
from datetime import timedelta, timezone
from pathlib import Path
from sqlalchemy import select
import httpx
from app.db import Session, SourceRefresh, init_db, now
from app.config import settings
from app.ingest import ingest, remotive
from app.sources import himalayas

log=logging.getLogger(__name__)
LOCK_PATH=Path(__file__).resolve().parent.parent/'data/refresh.lock'

def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

def refresh_sources(force=False):
    # Inter-process lock prevents CLI and app schedulers from importing concurrently.
    LOCK_PATH.parent.mkdir(exist_ok=True)
    with LOCK_PATH.open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {'status':'already_running'}
        results={}
        for source in ['remotive','himalayas']:
            with Session() as db:
                state=db.get(SourceRefresh,source)
                if not state:
                    state=SourceRefresh(source=source); db.add(state)
                interval=max(24,settings().jobs_refresh_hours)
                if not force and state.attempted_at and now()-aware(state.attempted_at)<timedelta(hours=interval):
                    results[source]={'status':'not_due'}; continue
                state.attempted_at=now(); state.status='running'; db.commit()
                try:
                    if source=='himalayas': rows,complete=himalayas(settings().himalayas_max_pages)
                    else: rows,complete=remotive(),True
                    counts=ingest(db,rows,source if complete else None)
                    state=db.get(SourceRefresh,source)
                    state.status='ok'; state.succeeded_at=now()
                    state.detail={**counts,'fetched':len(rows),'complete_snapshot':complete}
                    results[source]={'status':'ok',**state.detail}
                except Exception as exc:
                    db.rollback()
                    state=db.get(SourceRefresh,source); state.status='error'
                    # Do not persist exception text containing URLs/headers/credentials.
                    state.detail={'error':f'HTTP {exc.response.status_code}' if isinstance(exc,httpx.HTTPStatusError) else type(exc).__name__}
                    results[source]={'status':'error',**state.detail}
                    log.warning('Job refresh failed for %s (%s)',source,state.detail['error'])
                db.commit()
        return results

async def refresh_loop(stop):
    while not stop.is_set():
        await asyncio.to_thread(refresh_sources)
        try: await asyncio.wait_for(stop.wait(),timeout=3600)
        except TimeoutError: pass

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--force',action='store_true',help='Ignore the daily due check; respect provider limits')
    args=parser.parse_args();init_db()
    result=refresh_sources(force=args.force)
    print(json.dumps(result,indent=2))
    if any(isinstance(v,dict) and v.get('status')=='error' for v in result.values()): raise SystemExit(1)

if __name__=='__main__': main()
