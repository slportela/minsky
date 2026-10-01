"""Thin async base for bank.* stores: session + typed get/list. No generic CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from minsky_api.store.errors import StoreError

T = TypeVar("T", bound=SQLModel)


class BaseStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get(self, model: type[T], identity: Any) -> T | None:
        try:
            return await self._session.get(model, identity)
        except SQLAlchemyError as error:
            raise StoreError(f"failed to get {model.__name__}") from error

    async def _list(self, statement: SelectOfScalar[T]) -> tuple[T, ...]:
        try:
            result = await self._session.exec(statement)
            rows: Sequence[T] = result.all()
            return tuple(rows)
        except SQLAlchemyError as error:
            raise StoreError("failed to list rows") from error
