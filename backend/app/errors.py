class ApiError(Exception):
    """An expected failure with a stable code that clients and the assistant can branch on."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
