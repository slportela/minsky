"""Trusted test credentials deny missing, forged, anonymous, and expired sessions."""

import json

import pytest

from minsky_api.config import get_settings
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.http import resolve_session


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv(
        "MINSKY_TEST_SESSIONS", json.dumps({"credential": {"customer_id": "C1", "expires_at": "2099-01-01T00:00:00Z"}})
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "header,reason",
    [
        (None, "missing_credentials"),
        ("C1", "invalid_credentials"),
        ("Bearer C1", "invalid_credentials"),
        ("Bearer credential ", "invalid_credentials"),
    ],
)
def test_denied(header, reason):
    with pytest.raises(PermissionDenied, match=reason):
        resolve_session(header)


def test_customer_comes_from_server_mapping():
    assert resolve_session("Bearer credential").customer_id == "C1"


@pytest.mark.parametrize(
    "record,reason",
    [
        ({"customer_id": "C1", "expires_at": "2000-01-01T00:00:00Z"}, "session_expired"),
        ({"customer_id": "C1", "expires_at": "2099-01-01T00:00:00Z", "state": "anonymous"}, "invalid_credentials"),
    ],
)
def test_invalid_state(record, reason, monkeypatch):
    monkeypatch.setenv("MINSKY_TEST_SESSIONS", json.dumps({"credential": record}))
    get_settings.cache_clear()
    with pytest.raises(PermissionDenied, match=reason):
        resolve_session("Bearer credential")


@pytest.mark.parametrize("value", ["", "not json", '{"credential":{"customer_id":"C1","expires_at":"2099-01-01"}}'])
def test_bad_configuration_fails_closed(value, monkeypatch):
    monkeypatch.setenv("MINSKY_TEST_SESSIONS", value)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        resolve_session("Bearer credential")
