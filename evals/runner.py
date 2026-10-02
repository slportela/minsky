"""Scripted dev smoke: the real POST /api/chat/turn, no second model.

The customer turns are the case script. Slot extraction is filled from `known_info` when the
turn actually names the merchant, the amount or the transaction id — it does not call the LLM.
The state machine, the tools and the policy are the ones the API uses. Draft cases run only
with `--include-drafts` (schema: drafts are excluded until they are promoted).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from evals.graders import TrialGrade, grade_trial
from evals.schema import Case, Status, load_cases
from evals.world import MemoryBank, WorldFacts, build_bank, check_label, facts_from_case
from minsky_api.agent.extract import DisputeDetails
from minsky_api.api import chat as chat_api
from minsky_api.llm.client import LLMResult
from minsky_api.main import create_app

SCRIPTED_MODEL = "scripted-extract"


class ScriptedLLM:
    """Stands in for extract_dispute_details. Confirmations never reach it."""

    def __init__(self, facts: WorldFacts) -> None:
        self._facts = facts

    async def respond(
        self,
        instructions: str,
        messages: list[dict[str, str]],
        *,
        schema: type[Any] | None = None,
        reasoning_effort: str = "low",
        max_output_tokens: int = 256,
    ) -> LLMResult[DisputeDetails]:
        del instructions, schema, reasoning_effort, max_output_tokens
        text = messages[-1]["content"] if messages else ""
        details = _details_from_turn(text, self._facts)
        return LLMResult(
            text=details.model_dump_json(),
            parsed=details,
            model=SCRIPTED_MODEL,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
        )


def _details_from_turn(text: str, facts: WorldFacts) -> DisputeDetails:
    transaction_id = None
    if facts.other_transaction_id and facts.other_transaction_id in text:
        transaction_id = facts.other_transaction_id
    elif facts.transaction_id and facts.transaction_id in text:
        transaction_id = facts.transaction_id
    merchant = None
    amount = None
    if facts.merchant and facts.merchant.casefold() in text.casefold():
        merchant = facts.merchant
        amount = facts.amount
    elif facts.amount is not None and str(facts.amount) in text:
        amount = facts.amount
    says_not_me = facts.customer_says_not_me and any(
        phrase in text.casefold() for phrase in ("no fui yo", "no es mío", "no es mio")
    )
    return DisputeDetails(
        out_of_scope=False,
        merchant=merchant,
        amount=amount,
        customer_says_not_me=says_not_me,
        transaction_id=transaction_id,
    )


@contextmanager
def _patched(bank: MemoryBank, facts: WorldFacts) -> Iterator[None]:
    @asynccontextmanager
    async def _session() -> AsyncIterator[MemoryBank]:
        yield bank

    def _llm() -> ScriptedLLM:
        return ScriptedLLM(facts)

    with patch.object(chat_api, "session", _session), patch.object(chat_api, "LLM", _llm):
        yield


async def run_case(case: Case) -> TrialGrade:
    """Play `user_scenario.script` against POST /api/chat/turn and grade the end state."""
    if case.session.customer_id is None:
        raise ValueError(f"{case.id}: customer_id is required")
    if not case.user_scenario.script:
        raise ValueError(f"{case.id}: the scripted runner needs user_scenario.script")
    facts = facts_from_case(case)
    check_label(case, facts)
    bank = build_bank(case, facts)
    app = create_app()
    history: list[dict[str, str]] = []
    conversation_id: str | None = None
    # httpx's ASGI transport does not run the FastAPI lifespan, which is what attaches the case store.
    async with app.router.lifespan_context(app):
        with _patched(bank, facts):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                for turn in case.user_scenario.script:
                    history.append({"user": turn})
                    body: dict[str, Any] = {"messages": history}
                    if conversation_id is not None:
                        body["conversation_id"] = conversation_id
                    response = await client.post(
                        "/api/chat/turn",
                        json=body,
                        headers={"X-Minsky-Customer-Id": case.session.customer_id},
                    )
                    if response.status_code != 200:
                        raise RuntimeError(f"{case.id}: chat/turn {response.status_code} {response.text}")
                    payload = response.json()
                    conversation_id = payload["conversation_id"]
                    history = payload["messages"]
        messages = [_pair(item) for item in history]
        return grade_trial(case, app.state.cases, messages, facts)


def _pair(item: dict[str, str]) -> tuple[str, str]:
    if "user" in item:
        return ("user", item["user"])
    if "agent" in item:
        return ("agent", item["agent"])
    raise ValueError(f"message has neither user nor agent: {item}")


def _select(cases: list[Case], *, include_drafts: bool, ids: set[str] | None) -> list[Case]:
    selected = []
    for case in cases:
        if ids is not None and case.id not in ids:
            continue
        if case.status == Status.DRAFT and not include_drafts:
            continue
        if case.status == Status.RETIRED:
            continue
        info = case.user_scenario.known_info
        scripted = bool(case.user_scenario.script) and ("rule_id" in info or info.get("label_source") == "tool_denial")
        if not scripted:
            continue
        selected.append(case)
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases"))
    parser.add_argument("--include-drafts", action="store_true")
    parser.add_argument("--ids", default="", help="comma-separated case ids")
    args = parser.parse_args(argv)
    ids = {item for item in args.ids.split(",") if item} or None
    chosen = _select(load_cases(args.cases), include_drafts=args.include_drafts, ids=ids)
    if not chosen:
        print("0 cases")
        return 0
    failed = 0
    for case in chosen:
        grade = asyncio.run(run_case(case))
        mark = "pass" if grade.passed else "fail"
        print(f"{mark} {case.id} outcome={grade.observed_outcome} {'; '.join(grade.reasons)}")
        failed += not grade.passed
    print(f"{len(chosen) - failed}/{len(chosen)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
