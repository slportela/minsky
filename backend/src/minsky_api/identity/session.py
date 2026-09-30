"""Session object tools consume. HTTP API-key / OTP resolution comes later."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from minsky_api.identity.errors import PermissionDenied


class SessionState(StrEnum):
    VALID = "valid"
    EXPIRED = "expired"
    ANONYMOUS = "anonymous"


class ToolSession(BaseModel):
    """Authenticated (or not) session for a tool call. Aligns with evals.schema.Session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    state: SessionState
    customer_id: str | None = None

    @model_validator(mode="after")
    def _customer_for_authenticated(self) -> ToolSession:
        if self.state != SessionState.ANONYMOUS and not self.customer_id:
            raise ValueError("customer_id is required unless the session is anonymous")
        return self


def require_customer(session: ToolSession) -> str:
    """Return the session customer_id, or raise PermissionDenied for anonymous/expired/missing."""
    if session.state == SessionState.ANONYMOUS:
        raise PermissionDenied("anonymous")
    if session.state == SessionState.EXPIRED:
        raise PermissionDenied("expired")
    if not session.customer_id:
        raise PermissionDenied("missing_customer")
    return session.customer_id
