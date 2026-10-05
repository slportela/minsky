"""Demo operators (ADR 0015): server-provisioned, expiring credentials that may choose which customer to chat as.

A third class of credential, kept apart from customer sessions (identity.http) and staff (identity.staff): an
operator credential never resolves in the chat or the console, and a customer or staff credential never resolves
here. The customer id is not the credential. It is only chosen after the operator credential is proved, and the
choice is audited. Production does not have this: customers sign in with Cognito and OTP (docs/architecture.md).
"""

from datetime import UTC, datetime
from hmac import compare_digest

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from minsky_api.config import get_settings
from minsky_api.identity.errors import PermissionDenied


class OperatorCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    expires_at: AwareDatetime


class OperatorSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str


def resolve_operator(authorization: str | None) -> OperatorSession:
    """Bearer credential → operator, with explicit expiry and fail-closed configuration."""
    if not authorization:
        raise PermissionDenied("missing_credentials")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or token != token.strip() or not token.isascii():
        raise PermissionDenied("invalid_credentials")
    configured = get_settings().demo_operator_sessions
    if configured is None or not configured.get_secret_value().strip():
        raise RuntimeError("demo operator credentials are not configured")
    try:
        credentials = TypeAdapter(dict[str, OperatorCredential]).validate_json(configured.get_secret_value())
        if any(not key or not key.isascii() or key != key.strip() for key in credentials):
            raise ValueError("invalid credential key")
    except (ValidationError, ValueError) as exc:
        raise RuntimeError("invalid demo operator credential configuration") from exc
    for key, record in credentials.items():
        if compare_digest(key, token):
            if record.expires_at <= datetime.now(UTC):
                raise PermissionDenied("session_expired")
            return OperatorSession(operator_id=record.operator_id)
    raise PermissionDenied("invalid_credentials")
