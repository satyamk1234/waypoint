from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Text, JSON, Boolean, DateTime, ForeignKey, text, Integer, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from pgvector.sqlalchemy import Vector
from app.config import settings

def now():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    source: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(300))
    location: Mapped[str] = mapped_column(String(300))
    remote: Mapped[bool] = mapped_column(Boolean)
    employment_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    skills: Mapped[list] = mapped_column(JSON)
    salary: Mapped[str] = mapped_column(String(300), default='Not specified')
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_scope: Mapped[str] = mapped_column(String(20), default='unknown')
    countries: Mapped[list] = mapped_column(JSON, default=list)
    country_tokens: Mapped[str] = mapped_column(Text, default='||')
    timezones: Mapped[list] = mapped_column(JSON, default=list)
    published_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(200))
    embedding: Mapped[list] = mapped_column(JSON().with_variant(Vector(384), 'postgresql'))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SourceRefresh(Base):
    __tablename__ = 'source_refresh'
    source: Mapped[str] = mapped_column(String(40), primary_key=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default='never')
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Preference(Base):
    __tablename__ = 'preferences'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    values: Mapped[dict] = mapped_column(JSON, default=dict)

class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

engine = create_engine(settings().database_url, connect_args={'check_same_thread': False} if settings().database_url.startswith('sqlite') else {}, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)

def init_db():
    if engine.dialect.name == 'postgresql':
        with engine.begin() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    # Additive migration for existing SQLite/Postgres installations. No data is deleted.
    migrate_job_metadata(engine)
    Base.metadata.create_all(engine)
    if engine.dialect.name == 'postgresql':
        with engine.begin() as conn:
            conn.execute(text('CREATE INDEX IF NOT EXISTS jobs_embedding_hnsw ON jobs USING hnsw (embedding vector_cosine_ops)'))

def get_db():
    with Session() as db:
        yield db


def migrate_job_metadata(bind):
    inspector=inspect(bind)
    if 'jobs' not in inspector.get_table_names(): return
    columns={column['name'] for column in inspector.get_columns('jobs')}
    additions={
        'source_url':'TEXT', 'location_scope':"VARCHAR(20) NOT NULL DEFAULT 'unknown'",
        'countries':"JSON NOT NULL DEFAULT '[]'", 'country_tokens':"TEXT NOT NULL DEFAULT '||'",
        'timezones':"JSON NOT NULL DEFAULT '[]'", 'published_at':'INTEGER', 'expires_at':'INTEGER',
    }
    with bind.begin() as conn:
        for name,ddl in additions.items():
            if name not in columns: conn.execute(text(f'ALTER TABLE jobs ADD COLUMN {name} {ddl}'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_jobs_expires_at ON jobs (expires_at)'))
        if 'location_scope' not in columns:
            from app.locations import listed_eligibility, country_tokens
            import json
            for jid,location in conn.execute(text('SELECT id, location FROM jobs')).all():
                scope,countries=listed_eligibility(location)
                conn.execute(Job.__table__.update().where(Job.id==jid).values(location_scope=scope,countries=countries,country_tokens=country_tokens(countries),timezones=[]))
