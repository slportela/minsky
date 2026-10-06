"""Customer sessions issued to a demo operator for the customer they chose (ADR 0015).

Process-local, like the conversations they serve (the API runs as one worker): a restart ends them, which is fine
for a demo. Only the SHA-256 of a token is kept, so the store never holds a usable credential. Issuing is bounded
per operator per minute, in the total number of live sessions, and in time.
"""

from __future__ import annotations

import hashlib
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

TOKEN_PREFIX = "demo-s-"


class DemoSessionLimit(Exception):
    """Too many sessions asked for or alive: the caller answers 429."""


@dataclass(frozen=True)
class DemoSession:
    ref: str  # first 12 hex of the token digest: names the session in the audit trail, never usable as a credential
    customer_id: str
    operator_id: str
    expires_at: datetime

    @property
    def session_id(self) -> str:
        return f"demo:{self.operator_id}:{self.ref}"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class DemoSessionStore:
    def __init__(
        self,
        *,
        ttl: timedelta,
        per_minute: int,
        max_active: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = ttl
        self._per_minute = per_minute
        self._max_active = max_active
        self._clock = clock
        self._sessions: dict[str, DemoSession] = {}
        self._issued: dict[str, deque[datetime]] = {}

    def issue(self, *, operator_id: str, customer_id: str) -> tuple[str, DemoSession]:
        now = self._clock()
        self._purge(now)
        recent = self._issued.setdefault(operator_id, deque())
        while recent and recent[0] <= now - timedelta(minutes=1):
            recent.popleft()
        live = sum(1 for session in self._sessions.values() if session.expires_at > now)
        if len(recent) >= self._per_minute or live >= self._max_active:
            raise DemoSessionLimit
        token = TOKEN_PREFIX + secrets.token_urlsafe(24)
        digest = _digest(token)
        session = DemoSession(
            ref=digest[:12], customer_id=customer_id, operator_id=operator_id, expires_at=now + self._ttl
        )
        self._sessions[digest] = session
        recent.append(now)
        return token, session

    def resolve(self, token: str) -> tuple[DemoSession | None, bool]:
        """(session, expired). An unknown token is (None, False)."""
        session = self._sessions.get(_digest(token))
        if session is None:
            return None, False
        if session.expires_at <= self._clock():
            return session, True
        return session, False

    def _purge(self, now: datetime) -> None:
        for digest in [d for d, s in self._sessions.items() if s.expires_at <= now - timedelta(hours=1)]:
            del self._sessions[digest]
