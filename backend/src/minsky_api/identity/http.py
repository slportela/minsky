"""Resolve trusted POC credentials; customer ids supplied by callers never authenticate."""

from datetime import UTC, datetime
from hmac import compare_digest

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from minsky_api.config import get_settings
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.session import SessionState, ToolSession


class TestCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    customer_id: str = Field(min_length=1)
    expires_at: AwareDatetime
    state: SessionState = SessionState.VALID


def resolve_session(authorization: str | None) -> ToolSession:
    """Server-provisioned bearer token → identity, with explicit expiry and fail-closed config."""
    if not authorization:
        raise PermissionDenied("missing_credentials")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or token != token.strip() or not token.isascii():
        raise PermissionDenied("invalid_credentials")
    configured = get_settings().test_sessions
    if configured is None or not configured.get_secret_value().strip():
        raise RuntimeError("test session credentials are not configured")
    try:
        sessions = TypeAdapter(dict[str, TestCredential]).validate_json(configured.get_secret_value())
        if any(not key or not key.isascii() or key != key.strip() for key in sessions):
            raise ValueError("invalid credential key")
    except (ValidationError, ValueError) as exc:
        raise RuntimeError("invalid test session configuration") from exc
    for index, (key, record) in enumerate(sessions.items()):
        if compare_digest(key, token):
            if record.state == SessionState.EXPIRED or record.expires_at <= datetime.now(UTC):
                raise PermissionDenied("session_expired")
            if record.state != SessionState.VALID:
                raise PermissionDenied("invalid_credentials")
            return ToolSession(session_id=f"test-session-{index}", state=record.state, customer_id=record.customer_id)
    raise PermissionDenied("invalid_credentials")
