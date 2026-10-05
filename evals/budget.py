"""Reserve a conservative, explicitly priced allowance before each paid model call."""

import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from multiprocessing.context import BaseContext
from typing import Any


class BudgetExceeded(RuntimeError):
    """The next call cannot fit within the run's allowance."""


@dataclass
class SpendBudget:
    cap_usd: Decimal
    input_per_million: Decimal
    output_per_million: Decimal
    reserved_usd: Decimal = Decimal(0)
    observed_usd: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        for value in (self.cap_usd, self.input_per_million, self.output_per_million):
            if not value.is_finite() or value <= 0:
                raise ValueError("budget and both token prices must be finite and positive")

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (input_tokens * self.input_per_million + output_tokens * self.output_per_million) / 1_000_000

    def reserve(self, instructions: str, messages: list[dict[str, Any]], output_tokens: int) -> Decimal:
        # UTF-8 bytes overestimate byte-tokenizer text tokens. The extra allowance covers
        # message framing and the structured-output schema. No retry refunds after errors.
        input_bound = len(instructions.encode()) + len(json.dumps(messages, ensure_ascii=False).encode()) + 8192
        allowance = self.cost(input_bound, output_tokens)
        if self.reserved_usd + allowance > self.cap_usd:
            raise BudgetExceeded("next call exceeds reserved run budget")
        self.reserved_usd += allowance
        return allowance

    def account(self, input_tokens: int, output_tokens: int, allowance: Decimal) -> None:
        charge = self.cost(input_tokens, output_tokens)
        self.observed_usd += charge
        if charge > allowance:
            # Unexpected accounting/tokenization must stop further calls, not bypass the cap.
            self.reserved_usd = self.cap_usd
            raise BudgetExceeded("provider usage exceeded the conservative call reservation")

    def report(self) -> dict[str, str]:
        return {
            "cap_usd": str(self.cap_usd),
            "reserved_usd": str(self.reserved_usd),
            "usage_cost_usd": str(self.observed_usd),
            "input_usd_per_million": str(self.input_per_million),
            "output_usd_per_million": str(self.output_per_million),
            "pricing_basis": "operator-supplied uncached token rates; not an invoice",
        }


_NANO = Decimal(10) ** 9  # shared counters are integers of 1e-9 USD: a Decimal cannot live in shared memory


def _nano_up(value: Decimal) -> int:
    return int((value * _NANO).to_integral_value(rounding=ROUND_CEILING))


class SharedSpendBudget:
    """The same allowance as SpendBudget, held in memory shared between worker processes.

    `--workers N` runs trials in N processes, and the cap is one number for all of them, so reserved and observed
    spend live in shared integers under one lock. Same rules as SpendBudget: a call is reserved before dispatch and
    refused if it does not fit, a reservation is never refunded, and usage above its reservation closes the budget.
    Amounts round up to a nano-dollar, so the cap is never exceeded by rounding.

    Pass it to the workers when they start (ProcessPoolExecutor `initargs`): shared memory travels by inheritance,
    not by pickling a message.
    """

    def __init__(
        self, cap_usd: Decimal, input_per_million: Decimal, output_per_million: Decimal, context: BaseContext
    ) -> None:
        for value in (cap_usd, input_per_million, output_per_million):
            if not value.is_finite() or value <= 0:
                raise ValueError("budget and both token prices must be finite and positive")
        self.cap_usd = cap_usd
        self.input_per_million = input_per_million
        self.output_per_million = output_per_million
        self._lock = context.Lock()
        self._reserved = context.Value("q", 0, lock=False)
        self._observed = context.Value("q", 0, lock=False)

    @property
    def reserved_usd(self) -> Decimal:
        return Decimal(self._reserved.value) / _NANO

    @property
    def observed_usd(self) -> Decimal:
        return Decimal(self._observed.value) / _NANO

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (input_tokens * self.input_per_million + output_tokens * self.output_per_million) / 1_000_000

    def reserve(self, instructions: str, messages: list[dict[str, Any]], output_tokens: int) -> Decimal:
        input_bound = len(instructions.encode()) + len(json.dumps(messages, ensure_ascii=False).encode()) + 8192
        allowance = self.cost(input_bound, output_tokens)
        with self._lock:
            if self._reserved.value + _nano_up(allowance) > _nano_up(self.cap_usd):
                raise BudgetExceeded("next call exceeds reserved run budget")
            self._reserved.value += _nano_up(allowance)
        return allowance

    def account(self, input_tokens: int, output_tokens: int, allowance: Decimal) -> None:
        charge = self.cost(input_tokens, output_tokens)
        with self._lock:
            self._observed.value += _nano_up(charge)
            if charge > allowance:
                # Unexpected accounting/tokenization must stop further calls in every worker, not bypass the cap.
                self._reserved.value = _nano_up(self.cap_usd)
                raise BudgetExceeded("provider usage exceeded the conservative call reservation")

    def report(self) -> dict[str, str]:
        return {
            "cap_usd": str(self.cap_usd),
            "reserved_usd": str(self.reserved_usd),
            "usage_cost_usd": str(self.observed_usd),
            "input_usd_per_million": str(self.input_per_million),
            "output_usd_per_million": str(self.output_per_million),
            "pricing_basis": "operator-supplied uncached token rates; not an invoice",
        }


Budget = SpendBudget | SharedSpendBudget
