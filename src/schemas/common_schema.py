from pydantic import BaseModel


class APIResponse[T](BaseModel):
    success: bool = True
    message: str = "Request completed successfully."
    data: T | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    message: str = "Request failed."
    errors: dict | list | None = None


class PaginatedResponse[T](BaseModel):
    success: bool = True
    message: str = "Request completed successfully."
    data: list[T] | None = None
    total: int = 0
    page: int = 1
    previous_page_url: str | None = None
    next_page_url: str | None = None
    size: int = 10


class paginatedlistResponse[T](BaseModel):
    success: bool = True
    message: str = "Request completed successfully."
    data: list[T] | None = None
    total: int = 0
