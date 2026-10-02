"""Offline L2 regressions through the chat route; scripted extraction, real policy/tools/SQL.

This bounded diagnostic does not evaluate the production model. No provider calls or secrets.
"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from evals.fixtures import FixtureBank
from evals.schema import Case, load_case
from minsky_api.agent.extract import DisputeDetails
from minsky_api.api import chat
from minsky_api.config import get_settings
from minsky_api.llm.client import LLMResult
from minsky_api.main import create_app
from minsky_api.policy.disputes import DisputeFacts, TxnStatus, decide
from minsky_api.store.models import CustomerComplaintStats, Product, Transaction
from minsky_api.tools.errors import ToolError


class ScriptedExtraction:
    async def respond(
        self, instructions: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> LLMResult[DisputeDetails]:
        text = messages[-1]["content"]
        amount = re.search(r"\b\d+\.\d{2}\b", text)
        details = DisputeDetails(
            merchant="Cafe" if "Cafe" in text else None, amount=Decimal(amount[0]) if amount else None
        )
        return LLMResult(
            text=details.model_dump_json(),
            parsed=details,
            model="scripted-regression",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )


async def run_case(case: Case) -> dict[str, Any]:
    from minsky_api.agent import orchestrator

    info = case.user_scenario.known_info
    customer = case.session.customer_id
    assert customer is not None
    rows: list[Any] = []
    if "transaction_id" in info:
        base: dict[str, Any] = dict(
            customer_id=customer,
            product_id=info["product_id"],
            currency="USD",
            amount_usd_source="native_usd",
            transaction_date=datetime.fromisoformat(info["transaction_date"]),
            transaction_status="Approved",
            is_fraud=False,
        )
        rows.append(
            Transaction(
                **base,
                transaction_id=info["transaction_id"],
                merchant_name=info["merchant"],
                amount=Decimal(info["amount"]),
                amount_usd=Decimal(info["amount_usd"]),
            )
        )
        for extra in json.loads(info.get("extra_transactions", "[]")):
            rows.append(
                Transaction(
                    **base,
                    transaction_id=extra["transaction_id"],
                    merchant_name=extra["merchant"],
                    amount=Decimal(extra["amount"]),
                    amount_usd=Decimal(extra["amount"]),
                )
            )
        rows.extend(
            [
                CustomerComplaintStats(customer_id=customer, is_repeat_complainer=False),
                Product(product_id=info["product_id"], customer_id=customer, is_card=True),
            ]
        )
    if rows:
        decision = decide(
            DisputeFacts(
                status=TxnStatus.APPROVED,
                transaction_date=datetime.fromisoformat(info["transaction_date"]).date(),
                amount_usd=float(info["amount_usd"]),
                is_fraud=False,
                existing_dispute_ref=None,
                repeat_complainer=False,
                customer_says_not_me=False,
            ),
            get_settings().today,
        )
        if decision.rule_id != info["rule_id"]:
            raise ValueError("regression fixture disagrees with policy")
    bank = FixtureBank(rows)

    @asynccontextmanager
    async def db() -> AsyncIterator[FixtureBank]:
        yield bank

    evaluate = orchestrator.evaluate_dispute
    calls = 0

    async def fault(ctx: Any, args: Any) -> Any:
        nonlocal calls
        calls += 1
        if case.tool_faults and calls == 1:
            raise ToolError("scripted transient failure")
        return await evaluate(ctx, args)

    mapping = json.dumps({"regression-token": {"customer_id": customer, "expires_at": "2099-01-01T00:00:00Z"}})
    app = create_app()
    history: list[dict[str, str]] = []
    responses: list[dict[str, Any]] = []
    conversation_id = None
    expected = [int(x) for x in info.get("expected_http_statuses", "").split(",") if x]
    auth_denial = info.get("label_source") == "authentication"
    reasons: list[str] = []
    try:
        with (
            patch.dict("os.environ", {"MINSKY_TEST_SESSIONS": mapping}),
            patch.object(chat, "session", db),
            patch.object(chat, "LLM", ScriptedExtraction),
            patch.object(orchestrator, "evaluate_dispute", fault),
        ):
            get_settings.cache_clear()
            async with app.router.lifespan_context(app):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    for index, turn in enumerate(case.user_scenario.script):
                        body: dict[str, Any] = {"messages": history + [{"user": turn}]}
                        if conversation_id is not None:
                            body["conversation_id"] = conversation_id
                        # The legacy header lets the exact same workload exercise the old baseline.
                        headers = {"X-Minsky-Customer-Id": customer}
                        if not auth_denial:
                            headers["Authorization"] = "Bearer regression-token"
                        response = await client.post("/api/chat/turn", json=body, headers=headers)
                        payload = response.json()
                        responses.append({"request": body, "status": response.status_code, "response": payload})
                        wanted = 401 if auth_denial else expected[index] if expected else 200
                        if response.status_code != wanted:
                            reasons.append(f"turn {index + 1}: expected HTTP {wanted}, got {response.status_code}")
                            break
                        if response.status_code == 200:
                            conversation_id = payload["conversation_id"]
                            history = payload["messages"]
                dispute = app.state.cases.get_dispute_by_transaction(
                    customer_id=customer, transaction_id=info.get("transaction_id", "")
                )
                if not auth_denial and dispute is None:
                    reasons.append("expected dispute missing")
                if auth_denial and app.state.cases.list_audit():
                    reasons.append("unauthenticated request reached tools")
                audit = [vars(record) for record in app.state.cases.list_audit()]
                if dispute is not None and not any(
                    dispute.dispute_id in message.get("agent", "") for message in history
                ):
                    reasons.append("dispute reference not communicated")
                result = {
                    "case_id": case.id,
                    "passed": not reasons,
                    "reasons": reasons,
                    "responses": responses,
                    "audit": audit,
                    "dispute": vars(dispute) if dispute else None,
                }
    finally:
        get_settings.cache_clear()
        bank.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).parent / "cases" / "dev"
    names = ("dispute-auth-header-denied-es", "dispute-clarify-retain-merchant-es", "dispute-policy-failure-retry-es")
    records = [asyncio.run(run_case(load_case(root / f"{name}.yaml"))) for name in names]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "mode": "offline-scripted",
        "model": "scripted-regression",
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "cases": {name: hashlib.sha256((root / f"{name}.yaml").read_bytes()).hexdigest() for name in names},
        "trials_per_case": 1,
        "database": "isolated SQLite (not PostgreSQL)",
        "trials": records,
    }
    args.output.write_text(json.dumps(metadata, default=str, indent=2))
    for record in records:
        print(record["case_id"], "pass" if record["passed"] else "fail", record["reasons"])
    print(f"{sum(record['passed'] for record in records)}/{len(records)} passed (offline scripted extraction)")
    return int(any(not record["passed"] for record in records))


if __name__ == "__main__":
    raise SystemExit(main())
