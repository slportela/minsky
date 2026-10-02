"""Reserve a conservative, explicitly priced allowance before each paid model call."""

import json
from dataclasses import dataclass
from decimal import Decimal


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

    def reserve(self, instructions: str, messages: list[dict[str, str]], output_tokens: int) -> Decimal:
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
