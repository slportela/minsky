"""HTTP layer (FastAPI routers): customer chat turns and the back-office console.

Chat turns: `api.chat` (customer test sessions). Case queue: `api.console` (staff credentials).
"""

from minsky_api.api.chat import router as chat_router
from minsky_api.api.console import router as console_router

__all__ = ["chat_router", "console_router"]
