from __future__ import annotations

from typing import Any, Generic, TypeVar

T = TypeVar("T")

DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 100


class PaginationParams:
    """Validated pagination input parameters."""

    def __init__(self, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> None:
        self.page = self._validate_page(page)
        self.page_size = self._validate_page_size(page_size)

    @staticmethod
    def _validate_page(page: int) -> int:
        if not isinstance(page, int) or isinstance(page, bool):
            raise TypeError("Page must be an integer")
        if page < 1:
            raise ValueError("Page must be at least 1")
        return page

    @staticmethod
    def _validate_page_size(page_size: int) -> int:
        if not isinstance(page_size, int) or isinstance(page_size, bool):
            raise TypeError("Page size must be an integer")
        if page_size < 1:
            raise ValueError("Page size must be at least 1")
        if page_size > MAX_PAGE_SIZE:
            raise ValueError(f"Page size cannot exceed {MAX_PAGE_SIZE}")
        return page_size


class PaginationMetadata:
    """Metadata describing a paginated dataset."""

    def __init__(self, total_items: int, page: int, page_size: int) -> None:
        self.total_items = self._validate_total_items(total_items)
        self.page = page
        self.page_size = page_size
        self.total_pages = self._calculate_total_pages()
        self.has_next = self.page < self.total_pages
        self.has_previous = self.page > 1 and self.total_pages > 0
        self.next_page = self.page + 1 if self.has_next else None
        self.previous_page = self.page - 1 if self.has_previous else None

    @staticmethod
    def _validate_total_items(total_items: int) -> int:
        if not isinstance(total_items, int) or isinstance(total_items, bool):
            raise TypeError("Total items must be an integer")
        if total_items < 0:
            raise ValueError("Total items cannot be negative")
        return total_items

    def _calculate_total_pages(self) -> int:
        if self.total_items == 0:
            return 0
        return (self.total_items + self.page_size - 1) // self.page_size

    def to_dict(self) -> dict[str, Any]:
        """Return the pagination metadata as a dictionary."""
        return {
            "page": self.page,
            "page_size": self.page_size,
            "total_items": self.total_items,
            "total_pages": self.total_pages,
            "has_next": self.has_next,
            "has_previous": self.has_previous,
            "next_page": self.next_page,
            "previous_page": self.previous_page,
        }


class PaginatedResponse(Generic[T]):
    """Container for paginated data and response metadata."""

    def __init__(self, items: list[T], metadata: PaginationMetadata) -> None:
        self.items = items
        self.metadata = metadata

    def to_dict(self) -> dict[str, Any]:
        """Return the paginated response payload."""
        return {
            "items": self.items,
            "pagination": self.metadata.to_dict(),
        }


def paginate(items: list[T], page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> PaginatedResponse[T]:
    """Paginate a list of items and return response metadata."""
    params = PaginationParams(page=page, page_size=page_size)
    total_items = len(items)
    metadata = PaginationMetadata(total_items=total_items, page=params.page, page_size=params.page_size)

    start = (params.page - 1) * params.page_size
    end = start + params.page_size
    sliced_items = items[start:end]
    return PaginatedResponse(items=sliced_items, metadata=metadata)
