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


class UserNotFoundException(AppException):
    """
    Raised when a user is not found in the database.
    """

    def __init__(
        self,
        message: str = "User not found.",
        errors: dict[str, Any] | list[Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=404,
            errors=errors,
        )


class RepositoryError(AppException):
    """
    Raised when there is an error in the repository layer.
    """

    def __init__(
        self,
        message: str = "An error occurred in the repository.",
        errors: dict[str, Any] | list[Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=500,
            errors=errors,
        )


class BusinessRuleViolationError(AppException):
    """
    Raised when a business rule is violated.
    """

    def __init__(
        self,
        message: str = "Business rule violation.",
        errors: dict[str, Any] | list[Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=400,
            errors=errors,
        )


class AuthenticationError(AppException):
    """
    Raised when credentials are missing or invalid.
    The message is deliberately generic so it doesn't reveal which part was wrong.
    """

    def __init__(
        self,
        message: str = "Invalid email or password.",
        errors: dict[str, Any] | list[Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=401,
            errors=errors,
        )


class ConflictError(AppException):
    """
    Raised when a request conflicts with existing state, e.g. a duplicate email.
    """

    def __init__(
        self,
        message: str = "The resource already exists.",
        errors: dict[str, Any] | list[Any] | None = None,
    ):
        super().__init__(
            message=message,
            status_code=409,
            errors=errors,
        )
