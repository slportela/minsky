"""Isolated SQLite bank for offline API trials; execute the real read-store SQL."""

from typing import Any

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


class FixtureBank:
    """Async session surface over a per-trial SQL database, not a fabricated query result."""

    def __init__(self, rows: list[SQLModel]) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS bank")
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add_all(rows)
        self.session.commit()

    async def get(self, model: type[Any], identity: Any) -> Any:
        return self.session.get(model, identity)

    async def exec(self, statement: Any) -> Any:
        return self.session.exec(statement)

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()
