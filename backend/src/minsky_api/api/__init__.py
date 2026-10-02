"""HTTP layer (FastAPI routers): auth, chat turns, handoff queue for the console.

Chat turns: `api.chat`. Auth is still a POC header on the chat route.
"""

from minsky_api.api.chat import router as chat_router

__all__ = ["chat_router"]
