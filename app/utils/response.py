from __future__ import annotations

from typing import Any, Generic, TypeVar

T = TypeVar("T")


def success_response(data: T | None = None, message: str = "Success", status_code: int = 200) -> dict[str, Any]:
    """Return a standardized success response payload."""
    return {
        "success": True,
        "message": message,
        "data": data,
        "status_code": status_code,
    }


def error_response(message: str, status_code: int = 400, errors: Any | None = None) -> dict[str, Any]:
    """Return a standardized error response payload."""
    return {
        "success": False,
        "message": message,
        "errors": errors,
        "status_code": status_code,
    }


def validation_error_response(message: str = "Validation failed", errors: Any | None = None) -> dict[str, Any]:
    """Return a standardized validation error response payload."""
    return error_response(message=message, status_code=422, errors=errors)


def paginated_response(
    data: list[T],
    page: int,
    page_size: int,
    total_items: int,
    message: str = "Success",
) -> dict[str, Any]:
    """Return a standardized paginated response payload."""
    total_pages = (total_items + page_size - 1) // page_size if page_size else 0
    return {
        "success": True,
        "message": message,
        "data": data,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
        },
        "status_code": 200,
    }
