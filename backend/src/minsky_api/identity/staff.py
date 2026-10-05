"""Back-office agent identity for the console: server-provisioned, expiring bearer credentials.

Kept apart from customer sessions (identity.http): a customer credential never resolves here, so no
customer can read the case queue. Production replaces this with the bank's workforce SSO (Cognito
federation) behind the same function (docs/architecture.md).
"""

from datetime import UTC, datetime
from hmac import compare_digest

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from minsky_api.config import get_settings
from minsky_api.identity.errors import PermissionDenied


class StaffCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=64)
    expires_at: AwareDatetime


class StaffSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str


def resolve_staff(authorization: str | None) -> StaffSession:
    """Bearer credential → agent, with explicit expiry and fail-closed configuration."""
    if not authorization:
        raise PermissionDenied("missing_credentials")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or token != token.strip() or not token.isascii():
        raise PermissionDenied("invalid_credentials")
    configured = get_settings().staff_sessions
    if configured is None or not configured.get_secret_value().strip():
        raise RuntimeError("staff credentials are not configured")
    try:
        credentials = TypeAdapter(dict[str, StaffCredential]).validate_json(configured.get_secret_value())
        if any(not key or not key.isascii() or key != key.strip() for key in credentials):
            raise ValueError("invalid credential key")
    except (ValidationError, ValueError) as exc:
        raise RuntimeError("invalid staff credential configuration") from exc
    for key, record in credentials.items():
        if compare_digest(key, token):
            if record.expires_at <= datetime.now(UTC):
                raise PermissionDenied("session_expired")
            return StaffSession(agent_id=record.agent_id)
    raise PermissionDenied("invalid_credentials")
