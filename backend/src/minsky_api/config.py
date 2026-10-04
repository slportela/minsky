"""Settings from environment variables (prefix MINSKY_). Nothing here is a secret by default."""

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import NonNegativeInt, PositiveFloat, PositiveInt, SecretStr
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
    # Orchestrator budgets (docs/solution.md): stop runaway chats and clarify loops.
    max_turns: PositiveInt = 12
    max_clarify_attempts: PositiveInt = 2
    test_sessions: SecretStr | None = None
    # Back-office agents for /console: JSON mapping credential -> {agent_id, expires_at}. Separate from
    # customer sessions, so a customer credential can never read the case queue.
    staff_sessions: SecretStr | None = None
    # Where cases.* live: "postgres" in the compose stack (survives restarts), "memory" for tests and
    # direct runs without a database.
    cases_backend: Literal["memory", "postgres"] = "memory"
    db_pool_size: PositiveInt = 5
    db_max_overflow: NonNegativeInt = 10
    db_pool_timeout: PositiveInt = 30

    # Model provider: any OpenAI-compatible endpoint (ADR 0008). Interim: the OpenAI API. Bedrock later:
    # base URL https://bedrock-runtime.<region>.amazonaws.com/openai/v1, model us.openai.gpt-6-luna.
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-6-luna"
    llm_api_key: SecretStr | None = None
    llm_timeout_s: PositiveFloat = 30.0
    llm_max_retries: NonNegativeInt = 2


@lru_cache
def get_settings() -> Settings:
    # Opt in through the process environment before opening a file; a dotenv cannot select itself.
    env_file: Path | None = None
    if os.environ.get("MINSKY_ENVIRONMENT") == "local":
        configured_path = os.environ.get("MINSKY_ENV_FILE")
        env_file = (
            Path(configured_path).expanduser() if configured_path else Path(__file__).resolve().parents[3] / ".env"
        )
        if configured_path and not env_file.is_file():
            raise FileNotFoundError("MINSKY_ENV_FILE must point to an existing local configuration file")
    # Pydantic's synthesized signature omits these supported BaseSettings constructor options.
    return Settings(_env_file=env_file, _env_file_encoding="utf-8")  # pyright: ignore[reportCallIssue]
