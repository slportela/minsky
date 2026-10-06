"""Loopback-only smoke gateway: corrected chat UI, real API/Postgres, recorded model boundary.

This diagnostic serves a separately started frontend. It never changes the deployed stack,
bank rows or production routes. Test credentials are written separately with mode 0600.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import uvicorn
from fastapi import Request
from starlette.responses import Response

from evals.budget import SpendBudget
from evals.evidence import TrialRecord
from evals.gold_smoke import bind_gold_cases
from evals.runner import RecordedLLM, ScriptedLLM, _select
from evals.schema import load_case
from evals.world import facts_from_case
from minsky_api.agent import orchestrator
from minsky_api.api import chat as chat_api
from minsky_api.config import get_settings
from minsky_api.llm.client import LLM
from minsky_api.main import create_app
from minsky_api.tools.errors import ToolError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--web-url", default="http://127.0.0.1:3011")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extractor", choices=("scripted", "real"), default="scripted")
    parser.add_argument("--max-cost-usd", type=Decimal)
    parser.add_argument("--input-usd-per-million", type=Decimal)
    parser.add_argument("--output-usd-per-million", type=Decimal)
    parser.add_argument("--fail-policy-once", action="store_true", help="inject one recoverable policy-tool failure")
    args = parser.parse_args(argv)
    if urlparse(args.web_url).hostname not in {"localhost", "127.0.0.1"}:
        parser.error("frontend must be on loopback")
    budget = None
    settings = get_settings()
    if args.extractor == "real":
        if any(value is None for value in (args.max_cost_usd, args.input_usd_per_million, args.output_usd_per_million)):
            parser.error("real browser smoke requires cap and both token rates")
        if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value().strip():
            parser.error("model credential is unavailable in the inherited environment")
        budget = SpendBudget(args.max_cost_usd, args.input_usd_per_million, args.output_usd_per_million)
        print(f"Interactive smoke: each call reserves its estimate before dispatch; total cap USD {budget.cap_usd}")
    cases = asyncio.run(
        bind_gold_cases(
            _select(
                [load_case(p) for p in sorted(Path("evals/cases/dev").glob("*.yaml"))], include_drafts=True, ids=None
            )
        )
    )
    if not cases:
        parser.error("no gold cases available for the browser smoke")
    args.output.mkdir(parents=True, exist_ok=False)
    provisioned = {}
    credentials = {}
    for case in cases:
        token = uuid4().hex
        provisioned[token] = {
            "customer_id": case.session.customer_id,
            "state": case.session.state.value,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        credentials[case.id] = {"credential": token, "script": case.user_scenario.script}
    private = args.output / "test-credentials.json"
    descriptor = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(credentials, stream, indent=2, ensure_ascii=False)
    os.environ["MINSKY_TEST_SESSIONS"] = json.dumps(provisioned)
    get_settings.cache_clear()
    record = TrialRecord(case_id="browser-smoke")
    facts = [facts_from_case(case) for case in cases if "transaction_id" in case.user_scenario.known_info]

    class ScriptedDispatch:
        async def respond(self, instructions, messages, **kwargs):
            turn = messages[-1]["content"]
            selected = next((fact for fact in facts if fact.transaction_id and fact.transaction_id in turn), facts[0])
            return await ScriptedLLM(selected).respond(instructions, messages, **kwargs)

    # Retries stay 0: the spend budget reserves cost per call, and SDK retries would spend outside the cap.
    inner = (
        LLM(settings=settings.model_copy(update={"llm_max_retries": 0, "llm_timeout_s": 30}))
        if budget
        else ScriptedDispatch()
    )
    chat_api.LLM = lambda: RecordedLLM(inner, record, budget)  # type: ignore[assignment]
    if args.fail_policy_once:
        original_policy = orchestrator.evaluate_dispute
        pending = True

        async def policy_with_fault(ctx, tool_args):
            nonlocal pending
            if pending:
                pending = False
                raise ToolError("injected one-time policy failure")
            return await original_policy(ctx, tool_args)

        orchestrator.evaluate_dispute = policy_with_fault
    app = create_app()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def smoke_lifespan(application):
        try:
            async with original_lifespan(application):
                yield
        finally:
            private.unlink(missing_ok=True)
            if budget:
                await inner.client.close()  # type: ignore[attr-defined]

    app.router.lifespan_context = smoke_lifespan
    metadata = {
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "diff", "HEAD", "--name-only"], text=True).strip()),
        "mode": "live-browser" if budget else "scripted-browser",
        "database": "gold PostgreSQL read-only; process-local case writes",
        "model": settings.llm_model if budget else "scripted-extract",
        "budget": budget.report() if budget else None,
        "llm_timeout_s": 30 if budget else None,
        "tool_fault": "evaluate_dispute:error:first" if args.fail_policy_once else None,
        "limitations": (
            "local gateway excludes Caddy/TLS; partial graders, Spanish only; not a live result in scripted mode"
        ),
    }
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2))

    @app.middleware("http")
    async def evidence(request: Request, call_next):
        if request.url.path != "/api/chat/turn":
            return await call_next(request)
        body = await request.json()
        started = time.perf_counter()
        response = await call_next(request)
        content = b"".join([part async for part in response.body_iterator])
        record.requests.append(
            {
                "body": body,
                "http_status": response.status_code,
                "response": json.loads(content),
                "latency_ms": (time.perf_counter() - started) * 1000,
            }
        )
        record.audit = [vars(row) for row in app.state.cases.list_audit()]
        record.final_state = {}
        for case in cases:
            fact = facts_from_case(case)
            customer = case.session.customer_id or ""
            dispute = app.state.cases.get_dispute_by_transaction(
                customer_id=customer, transaction_id=fact.transaction_id or ""
            )
            block = app.state.cases.get_card_block(fact.product_id) if fact.product_id else None
            record.final_state[case.id] = {
                "dispute": vars(dispute) if dispute else None,
                "card_block": vars(block) if block else None,
            }
        refs = set(re.findall(r"HO-[0-9a-f]{12}", json.dumps(record.requests)))
        record.final_state["verified_handoffs"] = [
            vars(handoff) for ref in sorted(refs) if (handoff := app.state.cases.get_handoff(ref)) is not None
        ]
        snapshot = record.model_dump(mode="json", exclude={"status", "grade", "error_class", "error_message"})
        snapshot["grading"] = "manual browser diagnostic; no automatic TrialGrade"
        (args.output / "evidence.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
        if budget:
            (args.output / "budget.json").write_text(json.dumps(budget.report(), indent=2))
        return Response(content, status_code=response.status_code, headers=dict(response.headers))

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def frontend(path: str, request: Request):
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(
                request.method,
                f"{args.web_url}/{path}",
                params=request.query_params,
                content=await request.body(),
                headers={"accept-encoding": "identity"},
            )
        headers = {
            key: value for key, value in response.headers.items() if key not in {"transfer-encoding", "connection"}
        }
        return Response(response.content, status_code=response.status_code, headers=headers)

    print(f"Browser smoke: http://127.0.0.1:{args.port}/chat; private test credentials: {private}")
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    finally:
        private.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
