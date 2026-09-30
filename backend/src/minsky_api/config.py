"""Settings from environment variables (prefix MINSKY_). Nothing here is a secret by default."""

from datetime import date
from functools import lru_cache

from pydantic import PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MINSKY_", extra="ignore")

    environment: str = "local"
    database_url: str = "postgresql+psycopg://minsky:minsky@postgres:5432/minsky"
    aws_region: str = "us-east-1"
    # The data ends on 2026-06-18 (last transactions before 06:00, last complaints that morning): a fixed
    # "today" keeps policy windows and evals reproducible. Must equal the dbt var as_of_date.
    today: date = date(2026, 6, 18)
    max_message_chars: PositiveInt = 4000


@lru_cache
def get_settings() -> Settings:
    return Settings()
