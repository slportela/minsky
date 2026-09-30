"""Tool-layer denials and failures. Permission codes stay stable for tests and graders."""

from minsky_api.identity.errors import PermissionDenied


class ToolDenied(PermissionDenied):
    """Tool refused the call (session, ownership, confirmation, or missing row)."""


class ToolError(RuntimeError):
    """Unexpected tool failure after authorization (e.g. store down, missing read-back)."""
