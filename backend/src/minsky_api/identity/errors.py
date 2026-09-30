"""Identity / permission failures surfaced to tools."""


class PermissionDenied(PermissionError):
    """Session is not allowed to call a tool. reason is a stable machine code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
