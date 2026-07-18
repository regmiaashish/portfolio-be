from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "Request completed successfully."
    data: T | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    message: str = "Request failed."
    errors: dict | list | None = None
    
    
class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "Request completed successfully."
    data: list[T] | None = None
    total: int = 0
    page: int = 1
    previous_page_url: str | None = None
    next_page_url: str | None = None
    size: int = 10
    
class paginatedlistResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str = "Request completed successfully."
    data: list[T] | None = None
    total: int = 0