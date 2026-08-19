"""
spygram.exceptions
~~~~~~~~~~~~~~~~~~

Granular, modern exception hierarchy for Spygram (2026 standards).

Provides structured domain error handling across network transport, HTTP protocol,
Instagram-specific security challenges (checkpoints, action blocks), data extraction,
GraphQL query errors, caching, media downloads, and authentication.
"""

from __future__ import annotations

from typing import Any


class SpygramError(Exception):
    """
    Base class for all exceptions raised by Spygram.

    Supports contextual metadata dictionary and formatted string representation.

    :param message: Human-readable error description.
    :type message: str
    :param context: Optional dictionary containing debugging context (e.g. url, doc_id, user_id).
    :type context: dict[str, Any] | None
    """

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}

    def __str__(self) -> str:
        if self.context:
            ctx_str = ", ".join(f"{k}={v!r}" for k, v in self.context.items() if v is not None)
            return f"{self.message} ({ctx_str})"
        return self.message

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r}, context={self.context!r})"


# ==============================================================================
# Network & Transport Exceptions
# ==============================================================================


class NetworkError(SpygramError):
    """
    Base exception for physical network, socket, DNS, SSL, and transport errors.
    """
    pass


class NetworkConnectionError(NetworkError):
    """
    Raised when network connection attempts fail, drop, or reset after retries.
    """
    pass


class ConnectionTimeoutError(NetworkError):
    """
    Raised when a network socket or request times out.
    """
    pass


class ProxyError(NetworkError):
    """
    Raised when connecting through an HTTP/SOCKS proxy fails.
    """
    pass


# Backward compatibility alias
ConnectionError = NetworkConnectionError
TimeoutError = ConnectionTimeoutError


# ==============================================================================
# HTTP Protocol & Status Exceptions
# ==============================================================================


class HTTPStatusError(SpygramError):
    """
    Base exception for anomalous HTTP response status codes.

    :param message: Human-readable error description.
    :type message: str
    :param status_code: HTTP status code received.
    :type status_code: int
    :param url: Optional request URL.
    :type url: str | None
    :param response_body: Optional snippet of response body.
    :type response_body: str | None
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        url: str | None = None,
        response_body: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        ctx.update({"status_code": status_code, "url": url})
        super().__init__(message, context=ctx)
        self.status_code = status_code
        self.url = url
        self.response_body = response_body


class ClientError(HTTPStatusError):
    """
    Base exception for HTTP 4xx client errors (non-retriable by default).
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        url: str | None = None,
        response_body: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, url=url, response_body=response_body, context=context)


class BadRequestError(ClientError):
    """
    Raised when Instagram's API returns an HTTP 400 Bad Request or malformed request payload.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        url: str | None = None,
        response_body: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, url=url, response_body=response_body, context=context)


class ServerError(HTTPStatusError):
    """
    Raised when Instagram responds with an HTTP 5xx Server Error (transient, retriable).
    """

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        url: str | None = None,
        response_body: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, url=url, response_body=response_body, context=context)


class RateLimitError(ClientError):
    """
    Raised when Instagram responds with HTTP 429 (Too Many Requests).

    :param message: Human-readable error description.
    :type message: str
    :param retry_after: Suggested cooldown time in seconds before retrying.
    :type retry_after: float
    :param status_code: HTTP status code (defaults to 429).
    :type status_code: int
    :param url: Optional request URL.
    :type url: str | None
    """

    def __init__(
        self,
        message: str,
        retry_after: float = 60.0,
        status_code: int = 429,
        url: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        ctx["retry_after"] = retry_after
        super().__init__(message, status_code=status_code, url=url, context=ctx)
        self.retry_after = retry_after


class NotFoundError(ClientError):
    """
    Raised when a requested user, post, story, reel, or resource does not exist (HTTP 404).
    """

    def __init__(
        self,
        message: str,
        status_code: int = 404,
        url: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, url=url, context=context)


# ==============================================================================
# Authentication & Authorization Exceptions
# ==============================================================================


class AuthenticationError(ClientError):
    """
    Base exception for authentication, session validity, and authorization failures.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 401,
        url: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, url=url, context=context)


class LoginRequiredError(AuthenticationError):
    """
    Raised when an operation requires an authenticated session or when
    the current session has expired.
    """
    pass


class InvalidSessionError(AuthenticationError):
    """
    Raised when loaded session cookies are malformed, corrupt, or missing mandatory keys.
    """
    pass


class CheckpointError(AuthenticationError):
    """
    Raised when Instagram triggers a security checkpoint, challenge, or CAPTCHA verification.

    :param message: Human-readable error description.
    :type message: str
    :param checkpoint_url: Optional verification challenge URL.
    :type checkpoint_url: str | None
    :param challenge_type: Type of challenge (e.g. 'checkpoint_required', 'challenge_required').
    :type challenge_type: str | None
    """

    def __init__(
        self,
        message: str,
        checkpoint_url: str | None = None,
        challenge_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        ctx.update({"checkpoint_url": checkpoint_url, "challenge_type": challenge_type})
        super().__init__(message, status_code=403, context=ctx)
        self.checkpoint_url = checkpoint_url
        self.challenge_type = challenge_type


class ActionBlockedError(AuthenticationError):
    """
    Raised when Instagram returns 'feedback_required' indicating an action block or temporary restriction.
    """
    pass


class PermissionDeniedError(ClientError):
    """
    Raised when accessing a private account without following it, or restricted content (HTTP 403).
    """

    def __init__(
        self,
        message: str,
        status_code: int = 403,
        url: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, url=url, context=context)


# Backward compatibility aliases
FeedbackRequiredError = ActionBlockedError


# ==============================================================================
# Extraction, Parsing & GraphQL Exceptions
# ==============================================================================


class ScrapingError(SpygramError):
    """
    Base exception for payload parsing, schema changes, and extraction failures.
    """
    pass


class ExtractionError(ScrapingError):
    """
    Alias for ScrapingError.
    """
    pass


class GraphQLQueryError(ScrapingError):
    """
    Raised when a Polaris/Relay GraphQL query returns errors in its response envelope.

    :param message: Error summary message.
    :type message: str
    :param errors: Raw error list returned in the GraphQL response.
    :type errors: list[dict[str, Any]]
    """

    def __init__(
        self,
        message: str,
        errors: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        ctx = dict(context or {})
        ctx["error_count"] = len(errors) if errors else 0
        super().__init__(message, context=ctx)
        self.errors: list[dict[str, Any]] = errors or []


class PayloadMalformedError(ScrapingError):
    """
    Raised when an API response is missing expected fields due to Instagram schema modifications.
    """
    pass


class TokenExtractionError(ScrapingError):
    """
    Raised when security tokens (LSD, CSRF, App ID, Claim) cannot be extracted from HTML or headers.
    """
    pass


# ==============================================================================
# Download & Storage Exceptions
# ==============================================================================


class DownloadError(SpygramError):
    """
    Base exception for media file streaming, writing, and storage failures.
    """
    pass


class StorageFullError(DownloadError):
    """
    Raised when disk space is exhausted during media download.
    """
    pass


class IncompleteReadError(DownloadError):
    """
    Raised when a download stream terminates unexpectedly before receiving the complete payload.
    """
    pass


# ==============================================================================
# Cache Exceptions
# ==============================================================================


class CacheError(SpygramError):
    """
    Base exception for local SQLite cache database failures.
    """
    pass


class DatabaseLockError(CacheError):
    """
    Raised when the SQLite database is locked or busy under concurrency.
    """
    pass


class CacheCorruptedError(CacheError):
    """
    Raised when a cached database payload cannot be deserialized.
    """
    pass


# ==============================================================================
# Configuration Exceptions
# ==============================================================================


class ConfigurationError(SpygramError):
    """
    Raised when application configuration or filesystem parameters are invalid.
    """
    pass