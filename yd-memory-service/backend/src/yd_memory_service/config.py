from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YDM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Postgres
    database_url: str = "postgresql+asyncpg://ydm:ydm@localhost:5432/ydm"

    # LLM (optional — LearningModel in llm mode)
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Defaults for new Agent Spaces
    default_max_memories: int = 5000
    default_decay_per_day: float = 0.95
    default_min_weight: float = 0.1
    default_learning_mode: str = "heuristic"  # llm | heuristic | direct

    # zhparser config (pg_conf)
    zhparser_dict: str = "zhparser"


settings = Settings()
