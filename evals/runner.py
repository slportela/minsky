"""Offline dev API smoke with isolated SQL, scripted/real extraction, and durable evidence.

Scripted customer turns never expose hidden fixture facts to a production model. Scripted
extraction is an explicit diagnostic mode; it does not validate production prompts or models.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx2
from httpx import ASGITransport, AsyncClient
from openai import APITimeoutError

from evals.budget import SpendBudget
from evals.evidence import ToolEvidence, TrialRecord
from evals.graders import TrialGrade, grade_trial
from evals.metrics import Rate, language_rates
from evals.schema import Case, Split, Status, load_case
from evals.world import MemoryBank, WorldFacts, build_bank, check_label, facts_from_case
from minsky_api.agent import orchestrator
from minsky_api.agent.confirm import Confirmation
from minsky_api.agent.extract import DisputeDetails
from minsky_api.agent.speak import Speech
from minsky_api.api import chat as chat_api
from minsky_api.config import get_settings
from minsky_api.llm.client import LLM, LLMResult
from minsky_api.main import create_app
from minsky_api.tools.errors import ToolDenied, ToolError

SCRIPTED_MODEL = "scripted-extract"
_LLM_FAULT_REQUEST = httpx2.Request("POST", "https://llm.invalid/v1/responses")


class HTTPContractFailure(RuntimeError):
    """The system returned an observable response that violates the case contract."""


class ScriptedLLM:
    """Diagnostic extraction: only slots explicitly present in the current customer turn."""

    def __init__(self, facts: WorldFacts) -> None:
        self._facts = facts

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> LLMResult[Any]:
        text = messages[-1]["content"]
        if kwargs.get("schema") is Speech:
            speech = _scripted_speech(text)
            return LLMResult(
                text=speech.model_dump_json(),
                parsed=speech,
                model=SCRIPTED_MODEL,
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
            )
        if kwargs.get("schema") is Confirmation:
            confirmation = Confirmation(decision=_scripted_confirmation(text))
            return LLMResult(
                text=confirmation.model_dump_json(),
                parsed=confirmation,
                model=SCRIPTED_MODEL,
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
            )
        details = _details_from_turn(text, self._facts)
        return LLMResult(
            text=details.model_dump_json(),
            parsed=details,
            model=SCRIPTED_MODEL,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )


def _scripted_speech(text: str) -> Speech:
    """Diagnostic stand-in: the first allowed act, and the fact ids the customer must hear."""
    payload = json.loads(text)
    facts = payload.get("facts") or {}
    parts = [str(value) for value in facts.values() if not isinstance(value, bool)]
    body = " ".join(parts).strip() or str(payload.get("language") or "es")
    return Speech(act=payload["allowed"][0], text=body, claims_card_blocked=facts.get("card_blocked") is True)


def _scripted_confirmation(text: str) -> Literal["yes", "no", "unclear"]:
    """Diagnostic stand-in for agent.confirm. Same tokens as the consent boundary."""
    from minsky_api.agent.consent import explicit_no, explicit_yes

    if explicit_yes(text):
        return "yes"
    if explicit_no(text):
        return "no"
    return "unclear"


_NOT_ME_PHRASES = (
    "no fui yo",
    "no es mío",
    "no es mio",
    "no reconozco",
    "yo no hice",
    "não fui eu",
    "nao fui eu",
    "não reconheço",
    "nao reconheco",
    "eu não fiz",
    "eu nao fiz",
)


_APPROXIMATE = ("aproximadamente", "más o menos", "mas o menos", "cerca de", "alrededor de")
_DAYS_AGO = (("anteayer", 2), ("ayer", 1), ("hoy", 0))
_CURRENCIES = (("dólares", "USD"), ("dolares", "USD"), ("usd", "USD"), ("cop", "COP"), ("ars", "ARS"))
_KINDS = (
    ("retiro", "Withdrawal"),
    ("transferencia", "Transfer"),
    ("depósito", "Deposit"),
    ("pago", "Payment"),
    ("compra", "Purchase"),
)
_CATEGORIES = (("comida", "Food"), ("salud", "Health"), ("transporte", "Transport"), ("servicios", "Services"))
# A merchant the customer typed, as a model would extract it: the capitalized words after "en".
_TYPED_MERCHANT = re.compile(r"\ben ([A-ZÁÉÍÓÚÑ][\wáéíóúñ]*(?: [A-ZÁÉÍÓÚÑ][\wáéíóúñ]*)*)")


def _first_of(table: tuple[tuple[str, Any], ...], text: str) -> Any:
    folded = text.casefold()
    return next((value for word, value in table if re.search(rf"\b{word}\b", folded)), None)


def _details_from_turn(text: str, facts: WorldFacts) -> DisputeDetails:
    transaction_id = None
    if facts.other_transaction_id and facts.other_transaction_id in text:
        transaction_id = facts.other_transaction_id
    elif facts.transaction_id and facts.transaction_id in text:
        transaction_id = facts.transaction_id
    merchant = facts.merchant if facts.merchant and facts.merchant.casefold() in text.casefold() else None
    if merchant is None and (typed := _TYPED_MERCHANT.search(text)):
        merchant = typed[1]
    match = re.search(r"(?<![\w-])(\d+(?:[.,]\d{1,2})?)(?![\w-])", text)
    amount = Decimal(match[1].replace(",", ".")) if match else None
    says_not_me = any(phrase in text.casefold() for phrase in _NOT_ME_PHRASES)
    return DisputeDetails(
        merchant=merchant,
        amount=amount,
        currency=_first_of(_CURRENCIES, text),
        approximate=True if any(phrase in text.casefold() for phrase in _APPROXIMATE) else None,
        days_ago=_first_of(_DAYS_AGO, text),
        transaction_type=_first_of(_KINDS, text),
        category=_first_of(_CATEGORIES, text),
        customer_says_not_me=says_not_me,
        transaction_id=transaction_id,
    )


class FaultingLLM:
    """Raise a provider timeout on matching llm_faults before delegating."""

    def __init__(self, inner: Any, faults: list[Any]) -> None:
        self.inner = inner
        self.faults = faults
        self.calls = 0

    @property
    def client(self) -> Any:
        """The wrapped provider's client. The trial that owns this llm closes it, as it does for an unwrapped one."""
        return self.inner.client

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        self.calls += 1
        if any(item.on_call == self.calls for item in self.faults):
            raise APITimeoutError(request=_LLM_FAULT_REQUEST)
        return await self.inner.respond(instructions, messages, **kwargs)


class RecordedLLM:
    def __init__(self, inner: Any, record: TrialRecord, budget: SpendBudget | None = None) -> None:
        self.inner = inner
        self.record = record
        self.budget = budget

    async def respond(self, instructions: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        allowance = (
            self.budget.reserve(instructions, messages, kwargs.get("max_output_tokens", 1024)) if self.budget else None
        )
        event: dict[str, Any] = {
            "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
            "input": messages,
            "status": "started",
        }
        self.record.model_calls.append(event)
        try:
            result = await self.inner.respond(instructions, messages, **kwargs)
        except BaseException as error:
            event.update(status="error", error_class=type(error).__name__)
            raise
        event.update(
            {
                "instructions_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
                "input": messages,
                "model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms,
                "parsed": result.parsed.model_dump(mode="json") if result.parsed is not None else None,
                "status": "completed",
            }
        )
        if self.budget is not None and allowance is not None:
            self.budget.account(result.input_tokens, result.output_tokens, allowance)
        return result


@contextmanager
def _patched(
    bank: MemoryBank | None,
    facts: WorldFacts,
    record: TrialRecord,
    current: dict[str, Any],
    case: Case,
    extractor: str,
    budget: SpendBudget | None = None,
) -> Iterator[None]:
    @asynccontextmanager
    async def session() -> AsyncIterator[MemoryBank]:
        assert bank is not None
        yield bank

    # Retries stay 0: the spend budget reserves cost per call, and SDK retries would spend outside the cap.
    settings = get_settings().model_copy(update={"llm_max_retries": 0})
    inner: Any = LLM(settings=settings) if extractor == "real" else ScriptedLLM(facts)
    if case.llm_faults:
        inner = FaultingLLM(inner, case.llm_faults)
    if extractor == "real":
        current["llm"] = inner
    calls: dict[str, int] = {}

    def instrument(tool: str, call: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def wrapped(ctx: Any, args: Any = None) -> Any:
            state = current.get("active_state") or current.get("state")
            event = ToolEvidence(
                tool=tool,
                turn_index=current["index"],
                args=args.model_dump(mode="json") if args else {},
                outcome="error",
                customer_id=ctx.session.customer_id,
                prior_phase=state.phase.value if state else None,
                selected_transaction_id=state.selected_txn_id if state else None,
                selected_product_id=state.selected_product_id if state else None,
                user_text=current["text"],
                confirmation=state.confirmation if state else None,
            )
            record.tools.append(event)
            calls[tool] = calls.get(tool, 0) + 1
            try:
                fault = next(
                    (item for item in case.tool_faults if item.tool == tool and item.on_call == calls[tool]), None
                )
                if fault is not None:
                    if fault.mode != "error":
                        raise ValueError(f"unsupported fault mode: {fault.mode}")
                    raise ToolError(f"injected {tool} error")
                result = await call(ctx, args)
                event.outcome = "ok"
                event.result = result.model_dump(mode="json")
                return result
            except ToolDenied:
                event.outcome = "denied"
                raise

        return wrapped

    async def traced_run_turn(*args: Any, **kwargs: Any) -> Any:
        current["active_state"] = args[0]
        try:
            return await orchestrator.run_turn(*args, **kwargs)
        finally:
            current.pop("active_state", None)

    with ExitStack() as stack:
        if bank is not None:
            stack.enter_context(patch.object(chat_api, "session", session))
        stack.enter_context(patch.object(chat_api, "LLM", lambda: RecordedLLM(inner, record, budget)))
        stack.enter_context(patch.object(chat_api, "run_turn", traced_run_turn))
        for tool in (
            "get_transaction",
            "find_transactions",
            "evaluate_dispute",
            "open_dispute",
            "get_dispute",
            "block_card",
            "create_handoff",
        ):
            stack.enter_context(patch.object(orchestrator, tool, instrument(tool, getattr(orchestrator, tool))))
        try:
            yield
        finally:
            if extractor == "real":
                # The owning trial closes the async client below.
                current["llm"] = inner


async def run_trial(
    case: Case,
    *,
    extractor: str = "scripted",
    budget: SpendBudget | None = None,
    timeout_s: float = 120,
    database: str = "sqlite",
    legacy_auth_baseline: bool = False,
    allow_val: bool = False,
) -> TrialRecord:
    record = TrialRecord(case_id=case.id)
    started = time.perf_counter()
    bank = None
    current: dict[str, Any] = {}
    try:
        if extractor not in {"scripted", "real"} or database not in {"sqlite", "postgres"}:
            raise ValueError("unknown extractor or database mode")
        if extractor == "real" and budget is None:
            raise ValueError("real extraction requires a shared spend budget")
        if case.split == Split.TEST or (case.split == Split.VAL and not allow_val):
            raise ValueError("this diagnostic runner only runs dev cases (val only through evals.compare_systems)")
        if case.session.customer_id is None or not case.user_scenario.script:
            raise ValueError("scripted trial requires customer identity and user turns")
        facts = facts_from_case(case)
        check_label(case, facts)
        if (
            facts.label_source == "policy"
            and any(_details_from_turn(text, facts).customer_says_not_me for text in case.user_scenario.script)
            != facts.customer_says_not_me
        ):
            raise ValueError("scripted not-me signal disagrees with fixture policy facts")
        if any(fault.mode != "error" for fault in case.tool_faults):
            raise ValueError("only explicit error faults are supported in this partial smoke")
        bank = build_bank(case, facts) if database == "sqlite" else None
        record.world = asdict(facts)
        app = create_app()
        history: list[dict[str, str]] = []
        conversation_id = None
        token = uuid4().hex
        mapping = json.dumps(
            {
                token: {
                    "customer_id": case.session.customer_id,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "state": case.session.state.value,
                }
            }
        )
        expected = [
            int(value) for value in case.user_scenario.known_info.get("expected_http_statuses", "").split(",") if value
        ]
        if expected and len(expected) != len(case.user_scenario.script):
            raise ValueError("expected status count must match scripted turn count")
        with patch.dict("os.environ", {"MINSKY_TEST_SESSIONS": mapping}):
            get_settings.cache_clear()
            async with app.router.lifespan_context(app):
                if facts.existing_dispute and facts.transaction_id:
                    # Rule D04 needs a dispute opened before this conversation.
                    app.state.cases.create_dispute(
                        customer_id=case.session.customer_id,
                        transaction_id=facts.transaction_id,
                        reason="opened_in_an_earlier_conversation",
                    )
                try:
                    with _patched(bank, facts, record, current, case, extractor, budget):
                        async with (
                            asyncio.timeout(timeout_s),
                            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
                        ):
                            for index, turn in enumerate(case.user_scenario.script):
                                current.update(
                                    index=index,
                                    text=turn,
                                    state=app.state.conversations.get(UUID(conversation_id))
                                    if conversation_id
                                    else None,
                                )
                                body: dict[str, Any] = {"messages": [*history, {"user": turn}]}
                                if conversation_id:
                                    body["conversation_id"] = conversation_id
                                headers = {"Authorization": f"Bearer {token}"}
                                if legacy_auth_baseline or "legacy_header" in case.user_scenario.known_info:
                                    headers = {"X-Minsky-Customer-Id": case.session.customer_id}
                                request_evidence: dict[str, Any] = {"turn": index, "body": body}
                                record.requests.append(request_evidence)
                                response = await client.post("/api/chat/turn", json=body, headers=headers)
                                payload = response.json()
                                request_evidence.update(http_status=response.status_code, response=payload)
                                wanted = (
                                    401
                                    if facts.label_source == "authentication"
                                    else expected[index]
                                    if expected
                                    else 200
                                )
                                if response.status_code != wanted:
                                    if response.status_code >= 500 and any(
                                        call.get("status") == "error" for call in record.model_calls
                                    ):
                                        raise RuntimeError("provider error during HTTP request")
                                    raise HTTPContractFailure(
                                        f"turn {index + 1}: expected HTTP {wanted}, got {response.status_code}"
                                    )
                                if response.status_code == 200:
                                    conversation_id = payload["conversation_id"]
                                    history = payload["messages"]
                                record.messages = [_pair(item) for item in history]
                        stored = (
                            app.state.conversations.get(UUID(conversation_id)) if conversation_id is not None else None
                        )
                        grade = grade_trial(
                            case,
                            app.state.cases,
                            record.messages,
                            facts,
                            record.tools,
                            acts=list(stored.acts) if stored is not None else [],
                            rule_id=stored.rule_id if stored is not None else None,
                            claims_card_blocked=stored.claims_card_blocked if stored is not None else False,
                        )
                        record.grade = asdict(grade)
                        record.status = "passed" if grade.passed else "failed"
                finally:
                    record.audit = [vars(row) for row in app.state.cases.list_audit()]
                    record.final_state = {
                        "dispute": vars(dispute)
                        if (
                            dispute := app.state.cases.get_dispute_by_transaction(
                                customer_id=case.session.customer_id, transaction_id=facts.transaction_id or ""
                            )
                        )
                        else None,
                        "card_block": vars(block)
                        if facts.product_id and (block := app.state.cases.get_card_block(facts.product_id))
                        else None,
                        "handoffs": [
                            event.result["handoff"]
                            for event in record.tools
                            if event.tool == "create_handoff" and event.result is not None
                        ],
                    }
    except HTTPContractFailure as error:
        record.status = "failed"
        record.grade = {"passed": False, "components": {"http_contract": False}, "reasons": [str(error)]}
    except Exception as error:
        record.status = "error"
        record.error_class = type(error).__name__
        # Exception strings may contain provider details; retain HTTP status evidence instead.
        record.error_message = "Trial could not complete; inspect structured requests and tool evidence."
    finally:
        record.latency_ms = (time.perf_counter() - started) * 1000
        if bank is not None:
            bank.close()
        if "llm" in current:
            await current["llm"].client.close()
        get_settings.cache_clear()
    return record


async def run_case(case: Case) -> TrialGrade:
    record = await run_trial(case)
    if record.grade is None:
        raise RuntimeError(f"{case.id}: {record.error_class}; {record.error_message}")
    return TrialGrade(**record.grade)


def _pair(item: dict[str, str]) -> tuple[str, str]:
    return ("user", item["user"]) if "user" in item else ("agent", item["agent"])


def _select(cases: list[Case], *, include_drafts: bool, ids: set[str] | None) -> list[Case]:
    return [
        case
        for case in cases
        if case.split == Split.DEV
        and (ids is None or case.id in ids)
        and case.status != Status.RETIRED
        and (include_drafts or case.status != Status.DRAFT)
        and case.user_scenario.script
        and (
            "rule_id" in case.user_scenario.known_info
            or case.user_scenario.known_info.get("label_source") in {"tool_denial", "authentication"}
        )
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases/dev"))
    parser.add_argument("--include-drafts", action="store_true")
    parser.add_argument("--ids", default="")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--extractor", choices=("scripted", "real"), default="scripted")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--max-cost-usd", type=Decimal)
    parser.add_argument("--input-usd-per-million", type=Decimal)
    parser.add_argument("--output-usd-per-million", type=Decimal)
    parser.add_argument("--timeout-s", type=float, default=120)
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--database", choices=("sqlite", "postgres"), default="sqlite")
    parser.add_argument("--gold-cases", action="store_true")
    parser.add_argument(
        "--backend-revision", help="source revision label for an archived baseline; source files are hashed"
    )
    parser.add_argument(
        "--legacy-auth-baseline", action="store_true", help="only for the unmodified pre-credential API"
    )
    args = parser.parse_args(argv)
    ids = set(filter(None, args.ids.split(","))) or None
    root = args.cases / "dev" if (args.cases / "dev").is_dir() else args.cases
    chosen = _select(
        [load_case(path) for path in sorted(root.glob("*.yaml"))], include_drafts=args.include_drafts, ids=ids
    )
    if args.gold_cases:
        if args.database != "postgres":
            parser.error("--gold-cases requires --database postgres")
        from evals.gold_smoke import bind_gold_cases

        chosen = asyncio.run(bind_gold_cases(chosen))
    elif args.database == "postgres":
        parser.error("PostgreSQL mode requires data-bound --gold-cases")
    if not chosen or args.trials < 1 or not 0 < args.timeout_s <= 600:
        parser.error("select at least one runnable dev case and a positive trial count")
    budget = None
    if args.extractor == "real":
        if any(value is None for value in (args.max_cost_usd, args.input_usd_per_million, args.output_usd_per_million)):
            parser.error("real extraction requires --max-cost-usd and both explicit token-price flags")
        try:
            budget = SpendBudget(args.max_cost_usd, args.input_usd_per_million, args.output_usd_per_million)
        except ValueError as error:
            parser.error(str(error))
        from minsky_api.agent.prompts import render

        estimated = Decimal(0)
        for case in chosen:
            for turn in case.user_scenario.script or []:
                estimated += budget.cost(len(render("agent.extract.j2").encode()) + len(turn.encode()) + 8256, 256)
        estimated *= args.trials
        print(f"Conservative estimate: USD {estimated}; cap: USD {budget.cap_usd}; SDK retries: 0", flush=True)
        if estimated > budget.cap_usd:
            parser.error("estimate exceeds cap; reduce the workload or explicitly set a larger cap")
        if not args.estimate_only:
            key = get_settings().llm_api_key
            if key is None or not key.get_secret_value().strip():
                parser.error("model credential is unavailable in the inherited environment")
    if args.estimate_only:
        return 0
    output = args.output or Path("evals/runs") / f"smoke-{uuid4().hex[:12]}"
    output.mkdir(parents=True, exist_ok=False)
    from minsky_api.agent.prompts import _prompts_dir

    backend_root = Path(chat_api.__file__).resolve().parents[1]
    backend_hash = hashlib.sha256()
    for source in sorted(backend_root.rglob("*.py")):
        backend_hash.update(str(source.relative_to(backend_root)).encode())
        backend_hash.update(source.read_bytes())
    metadata = {
        "backend_revision_label": args.backend_revision,
        "backend_source_sha256": backend_hash.hexdigest(),
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "diff", "HEAD", "--name-only"], text=True).strip()),
        "mode": "live-extraction" if args.extractor == "real" else "offline-scripted",
        "model": get_settings().llm_model if args.extractor == "real" else SCRIPTED_MODEL,
        "budget": budget.report() if budget else None,
        "sdk_max_retries": 0 if budget else None,
        "trial_timeout_s": args.timeout_s,
        "legacy_auth_baseline": args.legacy_auth_baseline,
        "today": get_settings().today.isoformat(),
        "trials_per_case": args.trials,
        "cases": {case.id: hashlib.sha256(case.model_dump_json().encode()).hexdigest() for case in chosen},
        "prompts": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(_prompts_dir().glob("agent*.j2"))
        },
        "unsupported_safety": ["ungrounded_fact", "followed_injected_instruction"],
        "database": "gold PostgreSQL, read-only; case writes process-local"
        if args.database == "postgres"
        else "isolated SQLite; production PostgreSQL behavior not verified",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    passed = errors = 0
    language_rows: list[tuple[str, int, int]] = []
    language_cases: dict[str, set[str]] = {}
    language_trials: dict[str, int] = {}
    for case in chosen:
        for trial in range(args.trials):
            record = asyncio.run(
                run_trial(
                    case,
                    extractor=args.extractor,
                    budget=budget,
                    timeout_s=args.timeout_s,
                    database=args.database,
                    legacy_auth_baseline=args.legacy_auth_baseline,
                )
            )
            (output / f"{case.id}-{trial}.json").write_text(record.model_dump_json(indent=2))
            filename = "errors.jsonl" if record.status == "error" else "results.jsonl"
            with (output / filename).open("a") as stream:
                stream.write(record.model_dump_json() + "\n")
            passed += record.status == "passed"
            errors += record.status == "error"
            graded = int(record.status != "error")
            language = str(case.tags.language)
            language_cases.setdefault(language, set()).add(case.id)
            language_trials[language] = language_trials.get(language, 0) + 1
            language_rows.append((language, int(record.status == "passed"), graded))
            print(record.status, case.id, record.grade.get("reasons", []) if record.grade else record.error_class)
    attempted = len(chosen) * args.trials
    by_language = {
        language: {
            "cases": len(language_cases.get(language, ())),
            "trials": language_trials.get(language, 0),
            "passed": rate.numerator,
            "graded": rate.denominator,
            "rate": str(rate),
        }
        for language, rate in language_rates(language_rows).items()
    }
    summary = {
        "passed": passed,
        "attempted": attempted,
        "errors": errors,
        "graded": attempted - errors,
        "by_language": by_language,
        "provider_cost_usd": str(budget.observed_usd) if budget else 0,
        "budget": budget.report() if budget else None,
        "limitation": (
            "partial safety checks and draft dev cases; not a headline result. "
            "Language slices count generated dev drafts; replies were not held out."
        ),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(
        f"{Rate(passed, attempted - errors)} graded; "
        f"infrastructure/configuration errors {errors}/{attempted}; artifacts {output}"
    )
    return int(passed != attempted)


if __name__ == "__main__":
    raise SystemExit(main())
