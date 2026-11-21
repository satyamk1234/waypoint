import os
os.environ['EMBEDDING_BACKEND']='hash'
os.environ['DATABASE_URL']='sqlite://'
os.environ['GROQ_API_KEY']=''
os.environ['JOBS_REFRESH_ENABLED']='false'
import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base, User, Preference
from app.ingest import ingest

@pytest.fixture
def db():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id='a'*32)); session.add(User(id='b'*32)); session.commit()
        session.add(Preference(user_id='a'*32,values={'real_only':False})); session.commit()
        yield session
    engine.dispose()

@pytest.fixture
def jobs():
    return json.loads((Path(__file__).resolve().parent.parent/'data/sample_jobs.json').read_text())

@pytest.fixture
def populated(db,jobs):
    ingest(db,jobs)
    return db
