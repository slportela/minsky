"""Engine factory uses pool settings from config and recreates when they change."""

from typing import Any

from minsky_api.config import get_settings
from minsky_api.store import db as store_db


def test_get_engine_uses_pool_settings_and_pre_ping(monkeypatch):
    get_settings.cache_clear()
    store_db.reset_engine_state()
    monkeypatch.setenv("MINSKY_DB_POOL_SIZE", "3")
    monkeypatch.setenv("MINSKY_DB_MAX_OVERFLOW", "7")
    monkeypatch.setenv("MINSKY_DB_POOL_TIMEOUT", "11")
    get_settings.cache_clear()

    created: list[dict[str, Any]] = []

    def fake_create_async_engine(url: str, **kwargs: Any) -> Any:
        created.append({"url": url, "kwargs": kwargs})

        class _SyncEngine:
            def dispose(self) -> None:
                return None

        class _Engine:
            sync_engine = _SyncEngine()

            async def dispose(self) -> None:
                return None

        return _Engine()

    monkeypatch.setattr(store_db, "create_async_engine", fake_create_async_engine)
    try:
        engine = store_db.get_engine()
        assert engine is store_db.get_engine()
        assert len(created) == 1
        assert created[0]["kwargs"] == {
            "pool_size": 3,
            "max_overflow": 7,
            "pool_timeout": 11,
            "pool_pre_ping": True,
        }
        assert "postgresql+psycopg://" in str(created[0]["url"])

        monkeypatch.setenv("MINSKY_DB_POOL_SIZE", "4")
        get_settings.cache_clear()
        recreated = store_db.get_engine()
        assert recreated is store_db.get_engine()
        assert len(created) == 2
        assert created[1]["kwargs"]["pool_size"] == 4
    finally:
        store_db.reset_engine_state()
        get_settings.cache_clear()
        monkeypatch.delenv("MINSKY_DB_POOL_SIZE", raising=False)
        monkeypatch.delenv("MINSKY_DB_MAX_OVERFLOW", raising=False)
        monkeypatch.delenv("MINSKY_DB_POOL_TIMEOUT", raising=False)
