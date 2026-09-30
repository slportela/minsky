"""Settings from environment variables (prefix MINSKY_). Nothing here is a secret by default."""

from datetime import date
from functools import lru_cache

from pydantic import NonNegativeInt, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINSKY_", extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql+psycopg://minsky:minsky@postgres:5432/minsky"
    aws_region: str = "us-east-1"
    # The dataset ends 2026-06-17: a fixed "today" keeps policy windows and evals reproducible.
    today: date = date(2026, 6, 17)
    max_message_chars: PositiveInt = 4000
    db_pool_size: PositiveInt = 5
    db_max_overflow: NonNegativeInt = 10
    db_pool_timeout: PositiveInt = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
