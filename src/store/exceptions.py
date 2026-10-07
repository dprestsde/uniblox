class ServiceError(Exception):
    """Framework-independent error raised by domain services."""

    def __init__(self, code, message, *, status=400, retryable=False, details=None):
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.details = details
        super().__init__(message)


class OutputContractError(Exception):
    """Raised when an internal response DTO violates its public serializer schema."""
