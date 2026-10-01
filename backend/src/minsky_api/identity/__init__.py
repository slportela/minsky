"""Test identity: sessions for tools. Production swaps this for Cognito behind the same interface.

API-key HTTP auth and simulated OTP are not implemented yet; tools take a ToolSession directly.
"""

from minsky_api.identity.errors import PermissionDenied
from minsky_api.identity.session import SessionState, ToolSession, require_customer

__all__ = [
    "PermissionDenied",
    "SessionState",
    "ToolSession",
    "require_customer",
]
