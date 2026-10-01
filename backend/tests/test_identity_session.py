"""Unit tests for identity ToolSession and require_customer."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from minsky_api.identity import PermissionDenied, SessionState, ToolSession, require_customer


def test_valid_session_requires_customer_id():
    session = ToolSession(session_id="s1", state=SessionState.VALID, customer_id="C1")
    assert require_customer(session) == "C1"


def test_anonymous_session_allows_missing_customer():
    session = ToolSession(session_id="s1", state=SessionState.ANONYMOUS)
    with pytest.raises(PermissionDenied, match="anonymous"):
        require_customer(session)


def test_expired_and_valid_require_customer_id_at_construction():
    with pytest.raises(ValidationError):
        ToolSession(session_id="s1", state=SessionState.VALID)
    with pytest.raises(ValidationError):
        ToolSession(session_id="s1", state=SessionState.EXPIRED)


def test_expired_session_denied():
    session = ToolSession(session_id="s1", state=SessionState.EXPIRED, customer_id="C1")
    with pytest.raises(PermissionDenied, match="expired"):
        require_customer(session)
