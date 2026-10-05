"""Console API: staff-only access, queue order, claim and resolve, and the denials."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from minsky_api.config import get_settings
from minsky_api.main import create_app
from minsky_api.store.cases_memory import CaseRecord, InMemoryCasesBackend

STAFF = {"Authorization": "Bearer staff-ana"}
OTHER_STAFF = {"Authorization": "Bearer staff-luis"}


def _case(case_id: str, priority: str, queue: str, *, due_in: timedelta) -> CaseRecord:
    now = datetime.now(UTC)
    return CaseRecord(
        case_id=case_id,
        kind="handoff",
        customer_id="C1",
        rule_id="D06-possible-fraud" if queue == "fraud" else None,
        reason="possible_fraud" if queue == "fraud" else "out_of_scope",
        priority=priority,
        queue=queue,
        triage_reason="t",
        due_at=now + due_in,
        status="new",
        summary="s",
        facts={"verified": {"merchant": "Cafe", "amount_usd": "25.00"}, "customer_said": {}},
        actions=(),
        open_questions=("q?",),
        expected_resolution_days=15.0,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MINSKY_TEST_SESSIONS", '{"token-c1":{"customer_id":"C1","expires_at":"2099-01-01T00:00:00Z"}}')
    monkeypatch.setenv(
        "MINSKY_STAFF_SESSIONS",
        '{"staff-ana":{"agent_id":"ana","expires_at":"2099-01-01T00:00:00Z"},'
        '"staff-luis":{"agent_id":"luis","expires_at":"2099-01-01T00:00:00Z"},'
        '"staff-old":{"agent_id":"old","expires_at":"2020-01-01T00:00:00Z"}}',
    )
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as test_client:
        cases = InMemoryCasesBackend()
        cases.enqueue_case(_case("HO-low", "Medium", "general", due_in=timedelta(days=5)))
        cases.enqueue_case(_case("HO-fraud", "Critical", "fraud", due_in=timedelta(hours=4)))
        cases.enqueue_case(_case("HO-late", "Medium", "general", due_in=-timedelta(hours=1)))
        app.state.cases = cases
        yield test_client
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "missing_credentials"),
        ({"Authorization": "Bearer token-c1"}, "invalid_credentials"),  # a customer credential is not staff
        ({"Authorization": "Bearer staff-old"}, "session_expired"),
        ({"Authorization": "Basic staff-ana"}, "invalid_credentials"),
    ],
)
def test_console_denies_everyone_but_staff(client, headers, code):
    for method, path in (
        ("get", "/api/console/cases"),
        ("get", "/api/console/cases/HO-fraud"),
        ("post", "/api/console/cases/HO-fraud/claim"),
    ):
        response = getattr(client, method)(path, headers=headers)
        assert response.status_code == 401, path
        assert response.json()["code"] == code


def test_queue_is_ordered_and_counts_overdue(client):
    body = client.get("/api/console/cases", headers=STAFF).json()
    assert body["agent_id"] == "ana"
    assert [c["case_id"] for c in body["cases"]] == ["HO-fraud", "HO-late", "HO-low"]
    assert body["stats"]["open_cases"] == 3 and body["stats"]["overdue"] == 1
    assert body["stats"]["by_priority"]["Critical"] == 1
    fraud_only = client.get("/api/console/cases?queue=fraud", headers=STAFF).json()
    assert [c["case_id"] for c in fraud_only["cases"]] == ["HO-fraud"]


def test_detail_claim_and_resolve(client):
    detail = client.get("/api/console/cases/HO-fraud", headers=STAFF).json()
    assert detail["open_questions"] == ["q?"] and detail["case"]["merchant"] == "Cafe"
    assert client.post("/api/console/cases/HO-fraud/resolve", json={"note": "done"}, headers=STAFF).status_code == 409
    claimed = client.post("/api/console/cases/HO-fraud/claim", headers=STAFF).json()
    assert claimed["case"]["status"] == "in_progress" and claimed["case"]["assigned_to"] == "ana"
    taken = client.post("/api/console/cases/HO-fraud/claim", headers=OTHER_STAFF)
    assert taken.status_code == 409 and taken.json()["code"] == "case_conflict"
    done = client.post(
        "/api/console/cases/HO-fraud/resolve", json={"note": "Customer confirmed fraud; card replaced"}, headers=STAFF
    ).json()
    assert done["case"]["status"] == "resolved" and done["resolution_note"].startswith("Customer confirmed")


def test_unknown_case_and_bad_note(client):
    assert client.get("/api/console/cases/NOPE", headers=STAFF).json()["code"] == "case_not_found"
    assert client.post("/api/console/cases/NOPE/claim", headers=STAFF).status_code == 404
    assert client.post("/api/console/cases/HO-low/resolve", json={"note": ""}, headers=STAFF).status_code == 422


def test_staff_not_configured_fails_closed(client, monkeypatch):
    monkeypatch.delenv("MINSKY_STAFF_SESSIONS")
    get_settings.cache_clear()
    response = client.get("/api/console/cases", headers=STAFF)
    assert response.status_code == 503
