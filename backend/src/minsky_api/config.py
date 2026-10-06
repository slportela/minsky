"""Settings from environment variables (prefix MINSKY_). Nothing here is a secret by default."""

import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import NonNegativeInt, PositiveFloat, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEMO_ENVIRONMENTS = frozenset({"local", "demo"})


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
    # Replies in a row that are not a plain yes or no to the same question. At this one the conversation goes to a
    # person instead of asking again: with 3 the customer is told twice how to answer first.
    max_unclear_replies: PositiveInt = 3
    # Times the assistant may offer a human agent in one conversation when it cannot identify a charge. A "no" goes
    # back to asking for the charge; after the last offer the conversation ends without a case.
    max_handoff_offers: PositiveInt = 2
    test_sessions: SecretStr | None = None
    # Back-office agents for /console: JSON mapping credential -> {agent_id, expires_at}. Separate from
    # customer sessions, so a customer credential can never read the case queue.
    staff_sessions: SecretStr | None = None
    # Demo operators (ADR 0015): a credential that may choose which customer to chat as. Off by default, and the
    # server refuses to start with it on outside the local and demo environments. JSON mapping credential ->
    # {operator_id, expires_at}; a third class of credential, never accepted by the chat or the console.
    demo_operator_enabled: bool = False
    demo_operator_sessions: SecretStr | None = None
    demo_session_ttl_minutes: PositiveInt = 120
    demo_sessions_per_minute: PositiveInt = 20  # per operator
    demo_max_active_sessions: PositiveInt = 500
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
    # Read timeout per attempt. The live dev run's slowest of 424 calls took 4.8s (evals/runs, 2026-10-05).
    llm_timeout_s: PositiveFloat = 20.0
    llm_connect_timeout_s: PositiveFloat = 5.0
    # Retries are the SDK's own: exponential backoff with jitter, Retry-After honored, on timeouts,
    # connection errors, 408, 409, 429 and 5xx. Never on auth or other 4xx.
    llm_max_retries: NonNegativeInt = 2

    @model_validator(mode="after")
    def _demo_operator_only_where_it_is_a_demo(self) -> Self:
        if self.demo_operator_enabled and self.environment not in _DEMO_ENVIRONMENTS:
            allowed = sorted(_DEMO_ENVIRONMENTS)
            raise ValueError(f"MINSKY_DEMO_OPERATOR_ENABLED is only allowed in {allowed}, not {self.environment!r}")
        return self


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
