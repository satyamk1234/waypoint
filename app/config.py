from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    groq_api_key: str = ''
    groq_model: str = 'openai/gpt-oss-20b'
    database_url: str = 'sqlite:///./data/jobs.db'
    embedding_backend: Literal['fastembed', 'hash'] = 'fastembed'
    embedding_model: str = 'sentence-transformers/all-MiniLM-L6-v2'
    jobs_refresh_enabled: bool = True
    jobs_refresh_hours: int = 24
    himalayas_max_pages: int = 10
    himalayas_api_key: str = ''
    model_cache: str = '.cache/models'

@lru_cache
def settings():
    return Settings()
