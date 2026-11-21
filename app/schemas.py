from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Preferences(StrictModel):
    role: str | None = Field(None, max_length=150)
    location: str | None = Field(None, max_length=150)
    country: str | None = Field(None, max_length=100)
    real_only: bool = True
    remote: bool | None = None
    employment_type: Literal['internship','full_time','part_time','contract'] | None = None
    skills: list[str] = Field(default_factory=list, max_length=30)

class PreferencePatch(StrictModel):
    role: str | None = Field(None, max_length=150)
    location: str | None = Field(None, max_length=150)
    country: str | None = Field(None, max_length=100)
    real_only: bool = True
    remote: bool | None = None
    employment_type: Literal['internship','full_time','part_time','contract'] | None = None
    skills: list[str] | None = Field(None, max_length=30)

class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(6, ge=1, le=20)
    mode: Literal['bm25','vector','hybrid','rerank'] = 'rerank'

class ExplainArgs(StrictModel):
    job_id: str = Field(max_length=120)

class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=3000)

class AgentAnswer(StrictModel):
    message: str = Field(min_length=1, max_length=6000)
    job_ids: list[str] = Field(default_factory=list, max_length=20)

class JobInput(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=300)
    company: str = Field(min_length=1, max_length=300)
    location: str = Field(max_length=300)
    remote: bool
    employment_type: str = Field(max_length=50)
    description: str = Field(min_length=1, max_length=30000)
    url: HttpUrl
    skills: list[str] = Field(default_factory=list, max_length=50)
    salary: str = Field('Not specified', max_length=300)

    source_url: HttpUrl | None = None
    location_scope: Literal['worldwide','countries','unknown'] = 'unknown'
    countries: list[str] = Field(default_factory=list, max_length=250)
    timezones: list[float] = Field(default_factory=list, max_length=100)
    published_at: int | None = None
    expires_at: int | None = None
