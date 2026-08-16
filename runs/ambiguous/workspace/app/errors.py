"""Domain-level exceptions mapped to HTTP responses in main.py."""


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class InvalidURLError(AppError):
    status_code = 400


class AliasTakenError(AppError):
    status_code = 409


class NotFoundError(AppError):
    status_code = 404


class LinkExpiredError(AppError):
    status_code = 410


class RateLimitError(AppError):
    status_code = 429
