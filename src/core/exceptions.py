from typing import Any


class AppException(Exception):
    """
    Base exception for the application.
    All custom exceptions should inherit from this class.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        errors: dict[str, Any] | list[Any] | None = None,
    ):
        super().__init__(message)

        self.message = message
        self.status_code = status_code
        self.errors = errors


class EmailSendException(AppException):
    """
    Raised when an email cannot be sent.
    """

    def __init__(
        self,
        message: str = "Unable to send email. Please try again later.",
        errors: dict[str, Any] | list[Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=500,
            errors=errors,
        )