"""
spygram.iterator
~~~~~~~~~~~~~~~~

Resumable asynchronous feed and node iterator.

Provides lazy, buffered pagination across REST endpoints and GraphQL queries
with snapshot serialization, diagnostic logging, and date cutoff filtering.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime
import logging
from typing import Any, Generic, TypeVar

from spygram.models import PageResult, PaginationState

logger = logging.getLogger("spygram.iterator")

T = TypeVar("T")


class AsyncNodeIterator(Generic[T]):
    """
    Resumable asynchronous iterator for cursor-paginated REST and GraphQL streams.

    :param fetch_page: Coroutine callback taking a cursor string and returning a :class:`PageResult`.
    :type fetch_page: Callable[[str | None], Coroutine[Any, Any, PageResult[T]]]
    :param limit: Maximum number of items to yield (0 for unlimited).
    :type limit: int
    :param since: Optional UTC datetime cutoff to stop iteration early.
    :type since: datetime | None
    :param state: Optional prior PaginationState to resume from.
    :type state: PaginationState | None
    """

    def __init__(
        self,
        fetch_page: Callable[[str | None], Coroutine[Any, Any, PageResult[T]]],
        limit: int = 0,
        since: datetime | None = None,
        state: PaginationState | None = None,
    ) -> None:
        self._fetch_page = fetch_page
        self._limit = limit
        self._since = since
        self._cursor = state.cursor if state else None
        self._page_index = state.page_index if state else 0
        self._total_yielded = state.total_yielded if state else 0
        self._buffer: list[T] = []
        self._has_more = True
        self._exhausted = False

    @property
    def state(self) -> PaginationState:
        """
        Freeze and return the current pagination snapshot.

        :return: Current pagination state.
        :rtype: PaginationState
        """
        return PaginationState(
            cursor=self._cursor,
            page_index=self._page_index,
            total_yielded=self._total_yielded,
        )

    @property
    def total_yielded(self) -> int:
        """
        Total count of items produced by this iterator.

        :return: Number of yielded items.
        :rtype: int
        """
        return self._total_yielded

    def __aiter__(self) -> AsyncNodeIterator[T]:
        return self

    async def __anext__(self) -> T:
        if self._exhausted:
            raise StopAsyncIteration

        if 0 < self._limit <= self._total_yielded:
            self._exhausted = True
            raise StopAsyncIteration

        while not self._buffer:
            if not self._has_more:
                self._exhausted = True
                raise StopAsyncIteration

            logger.debug("Fetching page index %d (cursor: %s)", self._page_index, self._cursor)
            page = await self._fetch_page(self._cursor)
            self._page_index += 1
            self._cursor = page.next_cursor
            self._has_more = page.has_more and bool(page.next_cursor)

            if not page.items:
                logger.debug("Page index %d returned 0 items. Iterator exhausted.", self._page_index - 1)
                self._exhausted = True
                raise StopAsyncIteration

            logger.debug("Page index %d fetched %d items. (has_more=%s)", self._page_index - 1, len(page.items), self._has_more)

            for item in page.items:
                if self._since and hasattr(item, "taken_at"):
                    item_dt = getattr(item, "taken_at")
                    if isinstance(item_dt, datetime) and item_dt < self._since:
                        logger.debug("Item date %s is older than cutoff %s. Stopping pagination.", item_dt, self._since)
                        self._has_more = False
                        break
                self._buffer.append(item)

        if not self._buffer:
            self._exhausted = True
            raise StopAsyncIteration

        item = self._buffer.pop(0)
        self._total_yielded += 1
        return item
