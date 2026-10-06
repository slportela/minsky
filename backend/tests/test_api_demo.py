"""Demo operators (ADR 0015): choosing the customer of a demo conversation.

The operator credential is the only thing that authenticates. The customer id in the body says which customer to
chat as and is checked against bank.customers; every choice is audited with the operator; the feature is off by
default and refused outside the local and demo environments.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from minsky_api.agent.memory import ConversationStore
from minsky_api.agent.state import Phase
from minsky_api.config import Settings, get_settings
from minsky_api.identity.demo_sessions import DemoSessionLimit, DemoSessionStore
from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.http import resolve_session
from minsky_api.main import create_app
from minsky_api.store.cases_memory import InMemoryCasesBackend
from minsky_api.store.models import Customer, CustomerComplaintStats, Transaction

_FUTURE = "2099-01-01T00:00:00Z"
OPERATOR = {"Authorization": "Bearer op-equipo"}
CUSTOMERS = {"CLI-A": Customer(customer_id="CLI-A", first_name="Ana", country="Mexico")}


def _txn(
    transaction_id: str, amount: str, *, day: int, status: str = "Approved", merchant: str | None = "Cafe"
) -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        customer_id="CLI-A",
        product_id="P1",
        amount=Decimal(amount),
        currency="USD",
        amount_usd=Decimal(amount),
        amount_usd_source="native_usd",
        transaction_date=datetime(2026, 6, day, 9, 30),
        merchant_name=merchant,
        transaction_status=status,
        is_fraud=False,
    )


TXNS: list[Transaction] = []  # newest first, as the store returns them


class _FakeResult:
    def __init__(self, value: Any, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = rows or []

    def scalar(self) -> Any:
        return self._value

    def all(self) -> list[Any]:
        return self._rows


class _FakeDb:
    """bank.customers: `get` knows CLI-A; `execute` is the random pick and answers `random_pick`."""

    def __init__(self, random_pick: str | None) -> None:
        self.random_pick = random_pick

    async def get(self, model: type, identity: Any) -> Any:
        if model is Transaction:
            return next((t for t in TXNS if t.transaction_id == identity), None)
        if model is CustomerComplaintStats:
            return CustomerComplaintStats(customer_id=identity, is_repeat_complainer=False)
        return CUSTOMERS.get(identity)

    async def exec(self, statement: Any) -> _FakeResult:
        return _FakeResult(None, list(TXNS))

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        return _FakeResult(self.random_pick)


def _environment(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True, **extra: str) -> None:
    monkeypatch.setenv(
        "MINSKY_TEST_SESSIONS", json.dumps({"cust-cli-a": {"customer_id": "CLI-A", "expires_at": _FUTURE}})
    )
    monkeypatch.setenv("MINSKY_STAFF_SESSIONS", json.dumps({"staff-ana": {"agent_id": "ana", "expires_at": _FUTURE}}))
    monkeypatch.setenv(
        "MINSKY_DEMO_OPERATOR_SESSIONS",
        json.dumps(
            {
                "op-equipo": {"operator_id": "equipo", "expires_at": _FUTURE},
                "op-jurado": {"operator_id": "jurado", "expires_at": _FUTURE},
                "op-old": {"operator_id": "old", "expires_at": "2020-01-01T00:00:00Z"},
            }
        ),
    )
    monkeypatch.setenv("MINSKY_ENVIRONMENT", "demo")
    monkeypatch.setenv("MINSKY_DEMO_OPERATOR_ENABLED", "true" if enabled else "false")
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture
def harness(monkeypatch):
    TXNS.clear()
    _environment(monkeypatch)
    seen: dict[str, Any] = {"random_pick": "CLI-A", "sessions": []}

    @asynccontextmanager
    async def fake_session():
        yield _FakeDb(seen["random_pick"])

    async def fake_run_turn(state, text, ctx, llm):
        seen["sessions"].append(ctx.session)
        state.phase = Phase.DONE
        state.messages.append(("user", text))
        state.messages.append(("agent", "ok"))
        return state, "ok"

    monkeypatch.setattr("minsky_api.api.demo.session", fake_session)
    monkeypatch.setattr("minsky_api.api.chat.session", fake_session)
    monkeypatch.setattr("minsky_api.api.chat.run_turn", fake_run_turn)
    monkeypatch.setattr("minsky_api.api.chat.LLM", lambda: object())
    app = create_app()
    with TestClient(app) as client:
        app.state.cases = InMemoryCasesBackend()
        app.state.conversations = ConversationStore()
        yield app, client, seen
    get_settings.cache_clear()


def _choose(client: TestClient, body: dict[str, Any], headers: dict[str, str] = OPERATOR):
    return client.post("/api/demo/session", json=body, headers=headers)


# ---- off by default, and only in a demo environment -------------------------------------------------------------


def test_it_is_off_by_default_and_the_endpoints_do_not_exist(monkeypatch):
    _environment(monkeypatch, enabled=False)
    with TestClient(create_app()) as client:
        assert client.get("/api/demo/whoami", headers=OPERATOR).status_code == 404
        assert _choose(client, {"customer_id": "CLI-A"}).status_code == 404
    get_settings.cache_clear()


def test_the_setting_is_refused_outside_local_and_demo():
    Settings(environment="demo", demo_operator_enabled=True)
    Settings(environment="local", demo_operator_enabled=True)
    with pytest.raises(ValidationError, match="only allowed in"):
        Settings(environment="production", demo_operator_enabled=True)
    assert Settings(environment="production").demo_operator_enabled is False


# ---- who may choose ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers,code",
    [
        ({}, "missing_credentials"),
        ({"Authorization": "Bearer nope"}, "invalid_credentials"),
        ({"Authorization": "Bearer cust-cli-a"}, "invalid_credentials"),  # a customer credential
        ({"Authorization": "Bearer staff-ana"}, "invalid_credentials"),  # a staff credential
        ({"Authorization": "Bearer op-old"}, "session_expired"),
        ({"Authorization": "Basic op-equipo"}, "invalid_credentials"),
    ],
)
def test_only_a_live_operator_credential_may_choose_a_customer(harness, headers, code):
    app, client, _seen = harness
    for response in (
        client.get("/api/demo/whoami", headers=headers),
        _choose(client, {"customer_id": "CLI-A"}, headers),
    ):
        assert response.status_code == 401 and response.json()["code"] == code
    assert not app.state.cases.list_audit()


def test_an_operator_credential_is_not_a_customer_or_a_staff_credential(harness):
    _app, client, _seen = harness
    chat = client.post("/api/chat/turn", json={"messages": [{"user": "hola"}]}, headers=OPERATOR)
    assert chat.status_code == 401
    assert client.get("/api/console/cases", headers=OPERATOR).status_code == 401


def test_whoami_names_the_operator(harness):
    _app, client, _seen = harness
    assert client.get("/api/demo/whoami", headers=OPERATOR).json() == {"operator_id": "equipo"}


# ---- choosing --------------------------------------------------------------------------------------------------


def test_the_operator_chooses_a_customer_and_chats_as_that_customer(harness):
    app, client, seen = harness
    response = _choose(client, {"customer_id": "CLI-A"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["customer_id"], body["first_name"], body["operator_id"]) == ("CLI-A", "Ana", "equipo")
    assert body["credential"].startswith("demo-s-") and body["expires_at"]

    turn = client.post(
        "/api/chat/turn",
        json={"messages": [{"user": "hola"}]},
        headers={"Authorization": f"Bearer {body['credential']}"},
    )
    assert turn.status_code == 200, turn.text
    (tool_session,) = seen["sessions"]
    assert tool_session.customer_id == "CLI-A"  # from the session, never from the conversation
    assert tool_session.session_id.startswith("demo:equipo:")  # the actions in it name the operator
    assert body["credential"] not in tool_session.session_id


def test_every_choice_is_audited_with_the_operator_and_the_customer(harness):
    app, client, _seen = harness
    _choose(client, {"customer_id": "CLI-A"}, {"Authorization": "Bearer op-jurado"})
    (row,) = [r for r in app.state.cases.list_audit() if r.tool == "demo_choose_customer"]
    assert (row.tool, row.customer_id, row.outcome) == ("demo_choose_customer", "CLI-A", "ok")
    assert row.session_id.startswith("demo:jurado:") and row.reason == "operator=jurado"


def test_an_unknown_customer_is_refused_and_no_session_is_issued(harness):
    app, client, _seen = harness
    response = _choose(client, {"customer_id": "CLI-NOPE"})
    assert response.status_code == 404 and response.json()["code"] == "customer_not_found"
    assert not app.state.cases.list_audit()


def test_random_picks_a_customer_with_a_recent_charge(harness):
    _app, client, seen = harness
    CUSTOMERS["CLI-B"] = Customer(customer_id="CLI-B", first_name="Beto", country="Colombia")
    try:
        seen["random_pick"] = "CLI-B"
        response = _choose(client, {"random": True})
        assert response.status_code == 200 and response.json()["customer_id"] == "CLI-B"
        seen["random_pick"] = None
        assert _choose(client, {"random": True}).status_code == 404
    finally:
        CUSTOMERS.pop("CLI-B")


@pytest.mark.parametrize(
    "body",
    [{}, {"customer_id": "CLI-A", "random": True}, {"customer_id": "CLI A; drop"}, {"customer_id": ""}, {"x": 1}],
)
def test_the_body_must_name_one_customer_or_ask_for_random(harness, body):
    app, client, _seen = harness
    assert _choose(client, body).status_code in (400, 422)
    assert not app.state.cases.list_audit()


def test_a_session_for_another_operator_choice_never_changes_the_customer(harness):
    """Two sessions, two customers: each token resolves only to the customer it was issued for."""
    _app, client, seen = harness
    CUSTOMERS["CLI-B"] = Customer(customer_id="CLI-B", first_name="Beto", country="Colombia")
    try:
        first = _choose(client, {"customer_id": "CLI-A"}).json()["credential"]
        second = _choose(client, {"customer_id": "CLI-B"}).json()["credential"]
        for token in (first, second):
            client.post(
                "/api/chat/turn", json={"messages": [{"user": "hola"}]}, headers={"Authorization": f"Bearer {token}"}
            )
        assert [s.customer_id for s in seen["sessions"]] == ["CLI-A", "CLI-B"]
    finally:
        CUSTOMERS.pop("CLI-B")


def test_the_rate_limit_is_per_operator(harness, monkeypatch):
    monkeypatch.setenv("MINSKY_DEMO_SESSIONS_PER_MINUTE", "2")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        app.state.cases = InMemoryCasesBackend()
        for _ in range(2):
            assert _choose(client, {"customer_id": "CLI-A"}).status_code == 200
        limited = _choose(client, {"customer_id": "CLI-A"})
        assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
        assert _choose(client, {"customer_id": "CLI-A"}, {"Authorization": "Bearer op-jurado"}).status_code == 200


# ---- the session store -----------------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _store(clock: _Clock, **kwargs: Any) -> DemoSessionStore:
    return DemoSessionStore(
        ttl=kwargs.pop("ttl", timedelta(minutes=120)),
        per_minute=kwargs.pop("per_minute", 20),
        max_active=kwargs.pop("max_active", 500),
        clock=clock,
    )


def test_a_session_expires_and_an_expired_one_is_refused(monkeypatch):
    _environment(monkeypatch)
    clock = _Clock()
    store = _store(clock, ttl=timedelta(minutes=30))
    token, _ = store.issue(operator_id="equipo", customer_id="CLI-A")
    assert resolve_session(f"Bearer {token}", store).customer_id == "CLI-A"
    clock.now += timedelta(minutes=31)
    with pytest.raises(PermissionDenied, match="session_expired"):
        resolve_session(f"Bearer {token}", store)
    get_settings.cache_clear()


def test_a_forged_demo_token_or_one_without_the_feature_is_refused(monkeypatch):
    _environment(monkeypatch)
    store = _store(_Clock())
    token, _ = store.issue(operator_id="equipo", customer_id="CLI-A")
    for header in ("Bearer demo-s-forged", f"Bearer {token}x", "Bearer demo-s-"):
        with pytest.raises(PermissionDenied, match="invalid_credentials"):
            resolve_session(header, store)
    with pytest.raises(PermissionDenied, match="invalid_credentials"):
        resolve_session(f"Bearer {token}", None)  # the feature is off: no store, no demo session
    get_settings.cache_clear()


def test_the_store_never_keeps_a_usable_token():
    store = _store(_Clock())
    token, session = store.issue(operator_id="equipo", customer_id="CLI-A")
    assert token not in repr(store._sessions) and token not in session.session_id
    assert len(session.ref) == 12


def test_limits_per_minute_and_in_total_and_the_window_moves():
    clock = _Clock()
    store = _store(clock, per_minute=2, max_active=3)
    store.issue(operator_id="a", customer_id="C1")
    store.issue(operator_id="a", customer_id="C1")
    with pytest.raises(DemoSessionLimit):
        store.issue(operator_id="a", customer_id="C1")
    store.issue(operator_id="b", customer_id="C2")  # another operator has its own allowance
    with pytest.raises(DemoSessionLimit):  # but the total of live sessions is 3
        store.issue(operator_id="b", customer_id="C2")
    clock.now += timedelta(minutes=121)  # everything expired
    store.issue(operator_id="a", customer_id="C1")


def test_a_misconfigured_operator_list_fails_closed(monkeypatch):
    from minsky_api.identity.operator import resolve_operator

    for value in (
        "",
        "not json",
        '{"k":{"operator_id":"x","expires_at":"2099-01-01"}}',
        '{"k":{"operator_id":"a b","expires_at":"2099-01-01T00:00:00Z"}}',
    ):
        monkeypatch.setenv("MINSKY_DEMO_OPERATOR_SESSIONS", value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError):
            resolve_operator("Bearer k")
    get_settings.cache_clear()


# ---- what there is to dispute --------------------------------------------------------------------------------


def _charges(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return response.json()["recent_charges"]


def test_the_choice_comes_with_the_recent_charges_and_what_the_policy_does_with_each(harness):
    app, client, _seen = harness
    TXNS.extend(
        [
            _txn("T-BIG", "900.00", day=11, merchant=None),
            _txn("T-DECLINED", "40.00", day=9, status="Declined"),
            _txn("T-OK", "25.00", day=8),
        ]
    )
    charges = _charges(_choose(client, {"customer_id": "CLI-A"}))
    assert [c["transaction_id"] for c in charges] == ["T-BIG", "T-DECLINED", "T-OK"]  # newest first
    big, declined, ok = charges
    assert (ok["route"], ok["rule_id"], ok["date"], ok["amount"], ok["currency"]) == (
        "open_dispute",
        "D09-eligible",
        "2026-06-08",
        "25.00",
        "USD",
    )
    assert (
        ok["suggested_message"]
        == "Quiero reclamar un cargo de 25.00 USD en Cafe del 2026-06-08, el monto no es correcto."
    )
    assert ok["hint"] and "reclamo" in ok["hint"]
    assert big["route"] == "escalate_agent" and big["rule_id"].startswith("D07")
    assert "en None" not in big["suggested_message"] and " en " not in big["suggested_message"]  # no merchant
    assert declined["rule_id"].startswith("D01") and declined["status"] == "Declined"
    assert all(c["existing_dispute_id"] is None for c in charges)


def test_the_policy_is_read_through_the_tools_as_the_chosen_customer_and_nothing_is_written(harness):
    app, client, _seen = harness
    TXNS.append(_txn("T-OK", "25.00", day=8))
    _choose(client, {"customer_id": "CLI-A"})
    rows = app.state.cases.list_audit()
    assert {r.tool for r in rows} == {"demo_choose_customer", "get_transactions", "evaluate_dispute"}
    assert all(r.session_id.startswith("demo:equipo:") and r.customer_id == "CLI-A" for r in rows)
    assert not app.state.cases.list_cases()
    assert app.state.cases.get_dispute_by_transaction(customer_id="CLI-A", transaction_id="T-OK") is None


def test_a_charge_already_disputed_says_so(harness):
    app, client, _seen = harness
    TXNS.append(_txn("T-OK", "25.00", day=8))
    dispute = app.state.cases.create_dispute(customer_id="CLI-A", transaction_id="T-OK", reason="wrong_amount")
    (charge,) = _charges(_choose(client, {"customer_id": "CLI-A"}))
    assert charge["existing_dispute_id"] == dispute.dispute_id and charge["rule_id"].startswith("D04")


def test_at_most_the_newest_and_the_likely_ones_are_listed(harness):
    _app, client, _seen = harness
    TXNS.extend([_txn(f"T-BIG{i}", "900.00", day=20 - i) for i in range(6)])  # newest: all above the limit
    TXNS.extend([_txn(f"T-OK{i}", "25.00", day=10 - i) for i in range(6)])  # older: under it
    ids = [c["transaction_id"] for c in _charges(_choose(client, {"customer_id": "CLI-A"}))]
    assert ids == [f"T-BIG{i}" for i in range(4)] + [f"T-OK{i}" for i in range(4)]  # 4 newest + 4 likely, newest first


def test_if_the_charges_cannot_be_read_the_session_is_still_good_and_says_so(harness, monkeypatch):
    from minsky_api.tools.errors import ToolError

    _app, client, _seen = harness

    async def broken(ctx, args=None):
        raise ToolError("bank read failed")

    monkeypatch.setattr("minsky_api.api.demo.get_transactions", broken)
    response = _choose(client, {"customer_id": "CLI-A"})
    assert response.status_code == 200
    body = response.json()
    assert body["recent_charges"] is None and body["credential"].startswith("demo-s-")
