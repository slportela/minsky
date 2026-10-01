"""Store-layer failures. Misses return None from get/list helpers; mapping/driver errors raise."""


class StoreError(RuntimeError):
    """Raised when a read fails or a row cannot be mapped. Callers must handle it."""
