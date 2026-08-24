"""
spygram
~~~~~~~

A high-performance, asynchronous Instagram scraper and media archiver.

:copyright: (c) 2026 by Spygram.
:license: MIT.
"""

from __future__ import annotations

import logging as _logging

from spygram.cache import Cache
from spygram.client import InstagramClient
from spygram.config import AppConfig
from spygram.downloader import (
    Downloader,
    DownloadEvent,
    DownloadStatus,
)
from spygram.exceptions import (
    ActionBlockedError,
    AuthenticationError,
    BadRequestError,
    CacheCorruptedError,
    CacheError,
    CheckpointError,
    ClientError,
    ConfigurationError,
    ConnectionError,
    ConnectionTimeoutError,
    DatabaseLockError,
    DownloadError,
    ExtractionError,
    FeedbackRequiredError,
    GraphQLQueryError,
    HTTPStatusError,
    IncompleteReadError,
    InvalidSessionError,
    LoginRequiredError,
    NetworkConnectionError,
    NetworkError,
    NotFoundError,
    PayloadMalformedError,
    PermissionDeniedError,
    ProxyError,
    RateLimitError,
    ScrapingError,
    ServerError,
    SpygramError,
    StorageFullError,
    TimeoutError,
    TokenExtractionError,
)
from spygram.iterator import AsyncNodeIterator
from spygram.logger import mask_secret, sanitize_text, setup_logging
from spygram.models import (
    HighlightGroup,
    LocationInfo,
    MediaItem,
    MediaResource,
    MusicInfo,
    PageResult,
    PaginationState,
    Profile,
)

_logging.getLogger("spygram").addHandler(_logging.NullHandler())

__version__ = "2.0.0"
"""Package release version string."""

__all__ = [
    "InstagramClient",
    "Downloader",
    "DownloadEvent",
    "DownloadStatus",
    "Cache",
    "AppConfig",
    "AsyncNodeIterator",
    "PaginationState",
    "PageResult",
    "MediaItem",
    "MediaResource",
    "Profile",
    "HighlightGroup",
    "MusicInfo",
    "LocationInfo",
    "SpygramError",
    "NetworkError",
    "NetworkConnectionError",
    "ConnectionError",
    "ConnectionTimeoutError",
    "TimeoutError",
    "ProxyError",
    "HTTPStatusError",
    "ClientError",
    "BadRequestError",
    "ServerError",
    "RateLimitError",
    "NotFoundError",
    "AuthenticationError",
    "LoginRequiredError",
    "InvalidSessionError",
    "CheckpointError",
    "ActionBlockedError",
    "FeedbackRequiredError",
    "PermissionDeniedError",
    "ScrapingError",
    "ExtractionError",
    "GraphQLQueryError",
    "PayloadMalformedError",
    "TokenExtractionError",
    "DownloadError",
    "StorageFullError",
    "IncompleteReadError",
    "CacheError",
    "DatabaseLockError",
    "CacheCorruptedError",
    "ConfigurationError",
    "setup_logging",
    "sanitize_text",
    "mask_secret",
    "__version__",
]