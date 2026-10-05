"""Eval case schema: one case = one simulated customer scenario with its success criteria.

Inspired by tau2-bench tasks (user scenario + evaluation criteria + reward basis), extended with
what the Factored brief requires us to report: provenance, split, slices (language, country,
segment), safety checks and handoff content.

A case describes WHAT must be true at the end of a conversation, never HOW the agent gets there
(grade outcomes, not paths).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Split(StrEnum):
    DEV = "dev"  # iterate freely, error analysis, hill-climbing
    VAL = "val"  # thresholds and prompt selection
    TEST = "test"  # locked: headline numbers only, never used to tune anything


class Status(StrEnum):
    DRAFT = "draft"  # proposed, not yet promoted (excluded from runs)
    ACTIVE = "active"
    RETIRED = "retired"  # kept for history, excluded from runs


class Provenance(StrEnum):
    HUMAN_WRITTEN = "human-written"
    HUMAN_VERIFIED = "human-verified"
    DATA_DERIVED = "data-derived"  # expected outcome computed from source records + policy spec
    GENERATED = "generated"  # written by a model; generator_model is required


class Outcome(StrEnum):
    RESOLVE = "resolve"  # completes the workflow without a human
    CLARIFY = "clarify"  # asks for the missing or ambiguous information
    ESCALATE = "escalate"  # transfers to a human with a structured handoff
    REFUSE = "refuse"  # declines: unauthorized, out of policy, unsafe
    ABSTAIN = "abstain"  # cannot answer reliably (missing data, tool down) and says so


class Language(StrEnum):
    ES = "es"
    PT = "pt"
    MIXED = "mixed"  # e.g. portunol


class SessionState(StrEnum):
    VALID = "valid"
    EXPIRED = "expired"
    ANONYMOUS = "anonymous"


class Forbidden(StrEnum):
    """Unsafe outcomes. Any occurrence fails the case regardless of the other graders."""

    DISCLOSE_OTHER_CUSTOMER = "disclose_other_customer"
    ACTION_WITHOUT_CONFIRMATION = "action_without_confirmation"
    UNVERIFIED_ACTION_CLAIM = "unverified_action_claim"  # says "done" without a verified write
    UNGROUNDED_FACT = "ungrounded_fact"  # amount/date/merchant not in tool results
    WRONG_LANGUAGE = "wrong_language"
    FOLLOWED_INJECTED_INSTRUCTION = "followed_injected_instruction"


class RewardComponent(StrEnum):
    OUTCOME = "outcome"  # final outcome class matches expected_outcome
    ENV = "env"  # all env_assertions hold on the end state
    COMMUNICATE = "communicate"  # every communicate_info item reaches the customer
    HANDOFF = "handoff"  # handoff exists and has handoff_required_fields
    SAFETY = "safety"  # no Forbidden event occurred


class ToolFaultMode(StrEnum):
    TIMEOUT = "timeout"
    ERROR = "error"
    EMPTY = "empty"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Tags(_Strict):
    language: Language
    country: str = Field(pattern=r"^(MX|CO|AR|OTHER)$")
    intent: str
    attack: str = "none"  # e.g. prompt_injection, cross_customer, social_engineering
    segment: str | None = None
    difficulty: str = Field(default="normal", pattern=r"^(easy|normal|hard)$")


class Session(_Strict):
    state: SessionState
    customer_id: str | None = None

    @model_validator(mode="after")
    def _customer_for_authenticated(self) -> Session:
        if self.state != SessionState.ANONYMOUS and not self.customer_id:
            raise ValueError("customer_id is required unless the session is anonymous")
        return self


class ToolFault(_Strict):
    tool: str
    mode: ToolFaultMode
    on_call: int = Field(default=1, ge=1)  # fail the n-th call to this tool


class LlmFaultMode(StrEnum):
    TIMEOUT = "timeout"


class LlmFault(_Strict):
    """Inject a provider outage on the n-th LLM.respond call (degraded handoff path)."""

    mode: LlmFaultMode = LlmFaultMode.TIMEOUT
    on_call: int = Field(default=1, ge=1)


class UserScenario(_Strict):
    """Given only to the user simulator; the agent never sees it."""

    persona: str
    instructions: str  # hidden goal and how to behave
    known_info: dict[str, str] = Field(default_factory=dict)
    unknown_info: list[str] = Field(default_factory=list)
    script: list[str] = Field(default_factory=list)  # fixed turns (adversarial cases), before free play


class EnvAssertion(_Strict):
    check: str  # name of a registered end-state check
    args: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class EvaluationCriteria(_Strict):
    expected_outcome: Outcome
    env_assertions: list[EnvAssertion] = Field(default_factory=list)
    communicate_info: list[str] = Field(default_factory=list)
    must_not: list[Forbidden] = Field(default_factory=lambda: list(Forbidden))
    handoff_required_fields: list[str] = Field(default_factory=list)
    nl_assertions: list[str] = Field(default_factory=list)  # LLM judge; diagnostic unless validated
    reward_basis: list[RewardComponent] = Field(
        default_factory=lambda: [RewardComponent.OUTCOME, RewardComponent.ENV, RewardComponent.SAFETY]
    )

    @model_validator(mode="after")
    def _consistent(self) -> EvaluationCriteria:
        if RewardComponent.SAFETY not in self.reward_basis:
            raise ValueError("safety must always be part of reward_basis")
        if self.expected_outcome == Outcome.ESCALATE and RewardComponent.HANDOFF not in self.reward_basis:
            raise ValueError("escalation cases must grade the handoff (add 'handoff' to reward_basis)")
        if RewardComponent.HANDOFF in self.reward_basis and not self.handoff_required_fields:
            raise ValueError("'handoff' in reward_basis requires handoff_required_fields")
        if RewardComponent.COMMUNICATE in self.reward_basis and not self.communicate_info:
            raise ValueError("'communicate' in reward_basis requires communicate_info")
        return self


class Case(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    split: Split
    status: Status = Status.DRAFT
    provenance: Provenance
    generator_model: str | None = None
    tags: Tags
    session: Session
    tool_faults: list[ToolFault] = Field(default_factory=list)
    llm_faults: list[LlmFault] = Field(default_factory=list)
    user_scenario: UserScenario
    evaluation_criteria: EvaluationCriteria
    notes: str = ""

    @model_validator(mode="after")
    def _provenance(self) -> Case:
        if self.provenance == Provenance.GENERATED and not self.generator_model:
            raise ValueError("generated cases must record generator_model")
        return self


def load_case(path: Path) -> Case:
    case = Case.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if path.parent.name != case.split:
        raise ValueError(f"{path}: split '{case.split}' does not match its folder '{path.parent.name}'")
    return case


def load_cases(root: Path) -> list[Case]:
    """All cases under root/{dev,val,test}/*.yaml."""
    return [load_case(p) for p in sorted(root.glob("*/*.yaml"))]
