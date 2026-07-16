"""Safe, classified indexing failures."""


class IndexingError(RuntimeError):
    """A worker failure safe to persist without provider or repository data."""

    def __init__(self, *, code: str, message: str, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.safe_message = message
        self.retryable = retryable
