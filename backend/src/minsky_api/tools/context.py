"""Shared input for every tool call: session + bank DB session + cases backend."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel.ext.asyncio.session import AsyncSession

from minsky_api.identity.session import ToolSession
from minsky_api.store.cases import CasesBackend


@dataclass(frozen=True)
class ToolContext:
    session: ToolSession
    db: AsyncSession
    cases: CasesBackend
