"""Async SQLAlchemy engine and session factory for Postgres (pooled)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from minsky_api.config import get_settings

_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None
_engine_config: tuple[Any, ...] | None = None


def _pool_config() -> tuple[Any, ...]:
    settings = get_settings()
    return (
        settings.database_url,
        settings.db_pool_size,
        settings.db_max_overflow,
        settings.db_pool_timeout,
    )


def get_engine() -> AsyncEngine:
    """Return the pooled async engine, recreating it when pool settings change."""
    global _engine, _session_maker, _engine_config
    config = _pool_config()
    if _engine is not None and _engine_config == config:
        return _engine
    if _engine is not None:
        # Sync dispose so callers (and settings changes) need not be async.
        _engine.sync_engine.dispose()
        _engine = None
        _session_maker = None
    _engine = create_async_engine(
        config[0],
        pool_size=config[1],
        max_overflow=config[2],
        pool_timeout=config[3],
        pool_pre_ping=True,
    )
    _engine_config = config
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    engine = get_engine()
    if _session_maker is None:
        _session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return _session_maker


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    async with get_session_maker()() as db_session:
        yield db_session


async def dispose_engine() -> None:
    global _engine, _session_maker, _engine_config
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_maker = None
    _engine_config = None


def reset_engine_state() -> None:
    """Drop cached engine without awaiting (tests). Disposes the sync engine if present."""
    global _engine, _session_maker, _engine_config
    if _engine is not None:
        _engine.sync_engine.dispose()
    _engine = None
    _session_maker = None
    _engine_config = None
