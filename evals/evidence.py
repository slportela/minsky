"""Typed trial evidence kept independently of the candidate's claims."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ToolEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    turn_index: int
    args: dict[str, Any]
    result: dict[str, Any] | None = None
    outcome: Literal["ok", "denied", "error"]
    customer_id: str | None
    prior_phase: str | None = None
    selected_transaction_id: str | None = None
    selected_product_id: str | None = None
    user_text: str


class TrialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Literal["passed", "failed", "error"] = "error"
    grade: dict[str, Any] | None = None
    error_class: str | None = None
    error_message: str | None = None
    messages: list[tuple[str, str]] = []
    requests: list[dict[str, Any]] = []
    tools: list[ToolEvidence] = []
    audit: list[dict[str, Any]] = []
    final_state: dict[str, Any] = {}
    world: dict[str, Any] = {}
    model_calls: list[dict[str, Any]] = []
    latency_ms: float = 0
