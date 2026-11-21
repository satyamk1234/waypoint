from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from threading import Lock
import asyncio
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session as DBSession
from app.db import init_db, get_db, Job, User, Message, Preference, SourceRefresh
from app.config import settings
from app.schemas import SearchArgs, ChatRequest, PreferencePatch
from app.agent import get_preferences, update_preferences, run_agent, AgentError
from app.search import search, visible_jobs
from app.embeddings import model_id

STATIC=Path(__file__).parent/'static'
# One process-local lock bounds concurrent costly chat calls. Run one worker for this MVP.
chat_lock=Lock()

@asynccontextmanager
async def lifespan(app):
    init_db()
    from app.refresh import refresh_loop
    stop=asyncio.Event()
    task=asyncio.create_task(refresh_loop(stop)) if settings().jobs_refresh_enabled else None
    try: yield
    finally:
        stop.set()
        if task: await task

app=FastAPI(title='Waypoint · Job Matching Agent',version='1.0.0',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=STATIC),name='static')

@app.get('/',include_in_schema=False)
def index(): return FileResponse(STATIC/'index.html')

@app.get('/api/health')
def health(db:DBSession=Depends(get_db)):
    count=db.scalar(select(func.count()).select_from(Job).where(Job.active.is_(True)))
    indexed=db.scalar(select(func.count()).select_from(Job).where(Job.active.is_(True),Job.embedding_model==model_id()))
    live=db.scalar(visible_jobs(select(func.count()).select_from(Job)).where(Job.source!='sample',Job.embedding_model==model_id()))
    sources=[{'source':s.source,'status':s.status,'last_attempt':s.attempted_at,'last_success':s.succeeded_at,'detail':s.detail} for s in db.scalars(select(SourceRefresh))]
    return {'real_jobs':live,'sources':sources,'refresh_enabled':settings().jobs_refresh_enabled,'refresh_hours':max(24,settings().jobs_refresh_hours),'status':'ok','jobs':count,'searchable_jobs':indexed,'chat_configured':bool(settings().groq_api_key),'model':settings().groq_model,'embedding_model':model_id(),'database':db.bind.dialect.name}

@app.post('/api/sessions',status_code=201)
def session(db:DBSession=Depends(get_db)):
    uid=uuid4().hex
    db.add(User(id=uid)); db.commit()
    return {'session_id':uid}

def user_id(x_session_id:str=Header(default=''),db:DBSession=Depends(get_db)):
    if len(x_session_id)!=32 or db.get(User,x_session_id) is None:
        raise HTTPException(401,'Create a session first')
    return x_session_id

@app.get('/api/preferences')
def preferences(uid=Depends(user_id),db:DBSession=Depends(get_db)):
    return get_preferences(db,uid)

@app.patch('/api/preferences')
def patch_preferences(patch:PreferencePatch,uid=Depends(user_id),db:DBSession=Depends(get_db)):
    result=update_preferences(db,uid,patch); db.commit(); return result

@app.get('/api/history')
def history(uid=Depends(user_id),db:DBSession=Depends(get_db)):
    rows=list(db.scalars(select(Message).where(Message.user_id==uid).order_by(Message.id.desc()).limit(40)))[::-1]
    return [{'role':m.role,'content':m.content} for m in rows]

@app.delete('/api/session',status_code=204)
def clear_session(uid=Depends(user_id),db:DBSession=Depends(get_db)):
    db.execute(delete(Message).where(Message.user_id==uid))
    db.execute(delete(Preference).where(Preference.user_id==uid))
    db.execute(delete(User).where(User.id==uid)); db.commit()

@app.post('/api/search')
def search_jobs(args:SearchArgs,uid=Depends(user_id),db:DBSession=Depends(get_db)):
    try:
        return {'jobs':search(db,args.query,get_preferences(db,uid),args.limit,args.mode)}
    except (OSError,RuntimeError,ValueError) as exc:
        raise HTTPException(503,'Embedding model is unavailable. Run ingestion to download it, or check the server setup.') from exc

@app.post('/api/chat')
def chat(body:ChatRequest,uid=Depends(user_id),db:DBSession=Depends(get_db)):
    if not chat_lock.acquire(blocking=False):
        raise HTTPException(429,'Another chat request is running. Please retry shortly.')
    try:
        return run_agent(db,uid,body.message)
    except AgentError as exc:
        db.rollback(); raise HTTPException(exc.status,exc.message) from exc
    except (OSError,RuntimeError,ValueError) as exc:
        db.rollback(); raise HTTPException(503,'Search model unavailable. Check server setup and retry.') from exc
    finally:
        chat_lock.release()
