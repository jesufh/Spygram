"""
spygram.client
~~~~~~~~~~~~~~

Asynchronous Instagram Web API and dual GraphQL client.

Handles TLS fingerprint impersonation via :mod:`curl_cffi`, security token
bootstrapping (LSD, CSRF, Claims), rate limit management with adaptive jitter,
dual GraphQL transports (/api/graphql/ and /graphql/query/), granular exception
classification, and lazy asynchronous pagination over user media.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
import json
import logging
import random
import re
import time
from typing import Any

from curl_cffi.requests import AsyncSession, Response

from spygram.auth import extract_user_id_from_session
from spygram.cache import Cache
from spygram.exceptions import (
    ActionBlockedError,
    BadRequestError,
    CheckpointError,
    ConnectionTimeoutError,
    GraphQLQueryError,
    LoginRequiredError,
    NetworkConnectionError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
)
from spygram.iterator import AsyncNodeIterator
from spygram.logger import mask_secret, sanitize_text
from spygram.models import (
    HighlightGroup,
    MediaItem,
    PageResult,
    PaginationState,
    Profile,
    extract_hd_profile_pic_url,
)

logger = logging.getLogger("spygram.client")

DEFAULT_APP_ID = "936619743392459"
"""Default Instagram Web App ID header value."""

DEFAULT_ASBD_ID = "129477"
"""Default Instagram ASBD security header identifier."""

DOC_ID_POLARIS_CLIPS = "27838951732404191"
"""GraphQL persisted query document ID for Polaris clips connection."""

DOC_ID_POLARIS_MEDIA = "28166906066331987"
"""GraphQL persisted query document ID for Polaris logged-out media details."""


class RateLimiter:
    """
    Sliding window rate limiter with jittered delays.

    :param window_size: Time window duration in seconds.
    :type window_size: float
    :param max_requests: Maximum allowed requests within the time window.
    :type max_requests: int
    """

    def __init__(self, window_size: float = 600.0, max_requests: int = 50) -> None:
        self.window_size = window_size
        self.max_requests = max_requests
        self.timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def wait_if_needed(self) -> None:
        """
        Pause execution if the request frequency threshold is exceeded.
        """
        async with self._lock:
            now = time.monotonic()
            self.timestamps = [t for t in self.timestamps if now - t < self.window_size]

            if len(self.timestamps) >= self.max_requests:
                cooldown = self.window_size - (now - self.timestamps[0]) + 1.0
                if cooldown > 0:
                    logger.debug("RateLimiter threshold reached (%d reqs). Pausing %.1fs...", len(self.timestamps), cooldown)
                    await asyncio.sleep(cooldown)

            jitter = random.uniform(0.8, 1.5)
            await asyncio.sleep(jitter)
            self.timestamps.append(time.monotonic())


class InstagramClient:
    """
    High-level asynchronous Instagram client supporting REST and dual GraphQL transports.

    :param session_cookies: Optional dictionary of authenticated session cookies.
    :type session_cookies: dict[str, str] | None
    :param proxy: Optional HTTP or SOCKS5 proxy URL.
    :type proxy: str | None
    :param cache: Optional persistent cache repository.
    :type cache: Cache | None
    :param max_retries: Maximum number of request attempts for transient errors.
    :type max_retries: int
    :param timeout: HTTP request timeout in seconds.
    :type timeout: float
    """

    def __init__(
        self,
        session_cookies: dict[str, str] | None = None,
        proxy: str | None = None,
        cache: Cache | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")

        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.cache = cache
        self.max_retries = max_retries
        self.timeout = timeout
        self.rate_limiter = RateLimiter()

        self.session = AsyncSession(
            impersonate="chrome131",
            timeout=self.timeout,
            proxies=self.proxies,
            headers={
                "X-IG-App-ID": DEFAULT_APP_ID,
                "X-ASBD-ID": DEFAULT_ASBD_ID,
                "X-IG-WWW-Claim": "0",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.instagram.com/",
                "Origin": "https://www.instagram.com",
                "Accept": "*/*",
            },
        )

        self.user_id: str | None = None
        self.is_authenticated = False
        self.lsd_token: str | None = None
        self.app_id: str = DEFAULT_APP_ID
        self._bootstrapped = False
        self._bootstrap_lock = asyncio.Lock()

        if session_cookies:
            self.load_cookies(session_cookies)

    def load_cookies(self, cookies: dict[str, str]) -> None:
        """
        Load cookies into the underlying session and update authentication state.

        :param cookies: Cookie key-value pairs.
        :type cookies: dict[str, str]
        """
        for name, value in cookies.items():
            self.session.cookies.set(name, value, domain=".instagram.com")

        if cookies.get("sessionid"):
            self.is_authenticated = True
            self.user_id = extract_user_id_from_session(cookies)
            logger.debug("Loaded sessionid cookie. Authenticated as user_id: %s", self.user_id or "unknown")

        if csrf := cookies.get("csrftoken"):
            self.session.headers["X-CSRFToken"] = csrf

    async def __aenter__(self) -> InstagramClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """
        Close the underlying HTTP session.
        """
        await self.session.close()

    def _parse_html_tokens(self, html: str) -> dict[str, Any]:
        """
        Extract security tokens (LSD, CSRF, App ID, WWW-Claim) embedded in Instagram HTML.

        :param html: Raw HTML response string.
        :type html: str
        :return: Extracted token key-value mapping.
        :rtype: dict[str, Any]
        """
        extracted: dict[str, Any] = {}

        if m := re.search(r'\["LSD",\s*\[\],\s*\{"token":\s*"([^"]+)"\}', html):
            extracted["lsd"] = m.group(1)
        elif m := re.search(r'name="lsd"\s+value="([^"]+)"', html):
            extracted["lsd"] = m.group(1)

        if m := re.search(
            r'\["(?:CurrentUserInitialData|RelayAPIConfigDefaults)",\s*\[\],\s*\{.*?"(?:APP_ID|X-IG-App-ID)":\s*"(\d+)"',
            html,
        ):
            extracted["app_id"] = m.group(1)

        if m := re.search(r'\["InstagramSecurityConfig",\s*\[\],\s*\{"csrf_token":\s*"([^"]+)"\}', html):
            extracted["csrf_token"] = m.group(1)

        if m := re.search(r'"claim":\s*"(hmac_ttl\.[^"]+)"', html):
            extracted["claim"] = m.group(1)

        return extracted

    async def _bootstrap_session(self, target_url: str = "https://www.instagram.com/") -> dict[str, Any]:
        """
        Initialize session headers and acquire fresh anti-scraping tokens from Instagram.

        :param target_url: Bootstrap entrypoint URL.
        :type target_url: str
        :return: Extracted token dictionary.
        :rtype: dict[str, Any]
        """
        async with self._bootstrap_lock:
            if self._bootstrapped:
                return {}
            try:
                logger.debug("Bootstrapping session headers and LSD/CSRF tokens from %s", target_url)
                res = await self.session.get(target_url, allow_redirects=True)
                self._update_headers_from_response(res)
                tokens = self._parse_html_tokens(res.text)

                if lsd := tokens.get("lsd"):
                    self.lsd_token = lsd
                    logger.debug("Extracted LSD token: %s", mask_secret(lsd))
                if app_id := tokens.get("app_id"):
                    self.app_id = app_id
                    self.session.headers["X-IG-App-ID"] = app_id
                if csrf := tokens.get("csrf_token"):
                    if not self.session.cookies.get("csrftoken"):
                        self.session.headers["X-CSRFToken"] = csrf
                if claim := tokens.get("claim"):
                    self.session.headers["X-IG-WWW-Claim"] = claim

                self._bootstrapped = True
                return tokens
            except Exception as e:
                logger.warning("Session bootstrapping encountered non-fatal error: %s (continuing with default tokens)", e)
                return {}

    def _update_headers_from_response(self, res: Response) -> None:
        """
        Update dynamic claim and CSRF request headers from HTTP response headers and cookies.

        :param res: HTTP response object.
        :type res: Response
        """
        claim = res.headers.get("x-ig-set-www-claim") or res.headers.get("X-IG-Set-WWW-Claim")
        if claim:
            self.session.headers["X-IG-WWW-Claim"] = claim

        csrf = self.session.cookies.get("csrftoken")
        if csrf:
            self.session.headers["X-CSRFToken"] = csrf

    def _check_json_errors(self, res_json: dict[str, Any], url: str | None = None) -> None:
        """
        Inspect parsed JSON responses for GraphQL error envelopes and security checkpoints.

        :param res_json: Parsed response JSON dictionary.
        :type res_json: dict[str, Any]
        :param url: Request URL for diagnostic context.
        :type url: str | None
        :raises GraphQLQueryError: If the response contains GraphQL error items.
        :raises CheckpointError: If Instagram triggers a security challenge.
        :raises ActionBlockedError: If feedback is required due to rate or action blocks.
        :raises LoginRequiredError: If authentication is required.
        :raises BadRequestError: If status is reported as fail.
        """
        if not isinstance(res_json, dict):
            return

        if "errors" in res_json and isinstance(res_json["errors"], list) and res_json["errors"]:
            errors = res_json["errors"]
            first_msg = (
                errors[0].get("message", "Unknown GraphQL error")
                if isinstance(errors[0], dict)
                else str(errors[0])
            )
            logger.debug("GraphQL error detected: %s (query url: %s)", first_msg, url)
            raise GraphQLQueryError(f"Instagram GraphQL query error: {first_msg}", errors=errors, context={"url": url})

        msg = str(res_json.get("message", ""))
        chk_url = str(res_json.get("checkpoint_url", ""))
        status = str(res_json.get("status", ""))

        if chk_url or msg in ("checkpoint_required", "challenge_required"):
            logger.warning("Instagram security checkpoint triggered: %s (%s)", msg or "checkpoint", chk_url)
            raise CheckpointError(
                f"Instagram challenge required: {msg or 'checkpoint'}",
                checkpoint_url=chk_url or None,
                challenge_type=msg or "checkpoint",
                context={"url": url},
            )

        if msg == "feedback_required":
            logger.warning("Instagram action temporarily blocked: feedback_required (url: %s)", url)
            raise ActionBlockedError(f"Instagram action temporarily blocked: {msg}", context={"url": url})

        if msg == "login_required":
            logger.warning("Instagram login required (session invalid or expired)")
            raise LoginRequiredError("Session expired or authentication required", context={"url": url})

        if status and status == "fail":
            logger.debug("Instagram API returned status 'fail': %s", msg)
            raise BadRequestError(f"API returned failure: '{msg or 'Unknown error'}'", context={"url": url})

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """
        Execute an HTTP request with exponential backoff, rate limiting, and redirect management.

        :param method: HTTP method ('GET', 'POST', etc.).
        :type method: str
        :param url: Target request URL.
        :type url: str
        :param kwargs: Additional arguments passed to the underlying session.
        :return: JSON response payload.
        :rtype: dict[str, Any]
        :raises LoginRequiredError: If redirected to login wall or unauthenticated.
        :raises NotFoundError: If resource does not exist (HTTP 404).
        :raises RateLimitError: If rate limit quota is exhausted (HTTP 429).
        :raises PermissionDeniedError: If access is forbidden (HTTP 401/403).
        :raises ServerError: If Instagram returns a 5xx error after retries.
        :raises BadRequestError: If a client error 4xx occurs.
        :raises ConnectionTimeoutError: If the request times out after retries.
        :raises NetworkConnectionError: If transport failures persist after retries.
        """
        if not self._bootstrapped:
            await self._bootstrap_session()

        kwargs.setdefault("allow_redirects", False)
        attempts = 0
        backoff = 3.0

        clean_log_url = sanitize_text(url)

        while attempts < self.max_retries:
            attempts += 1
            await self.rate_limiter.wait_if_needed()

            current_method = method
            current_url = url
            current_kwargs = dict(kwargs)
            redirects_left = 5

            try:
                while True:
                    clean_log_url = sanitize_text(current_url)
                    logger.debug("HTTP %s %s (attempt %d/%d)", current_method, clean_log_url, attempts, self.max_retries)
                    res = await self.session.request(current_method, current_url, **current_kwargs)
                    self._update_headers_from_response(res)

                    if res.status_code in (301, 302, 303, 307, 308):
                        loc = res.headers.get("location", "")
                        if "accounts/login" in loc or "login" in loc:
                            logger.warning("Redirected to Instagram login wall (%s)", loc)
                            raise LoginRequiredError("Session expired or authentication required", url=current_url)

                        if loc:
                            if redirects_left <= 0:
                                raise NetworkConnectionError(f"Too many redirects for {current_url}")
                            redirects_left -= 1

                            if loc.startswith("/"):
                                loc = f"https://www.instagram.com{loc}"

                            logger.debug("Following redirect: %s -> %s", clean_log_url, loc)
                            current_url = loc
                            if res.status_code in (301, 302, 303) and current_method.upper() == "POST":
                                current_method = "GET"
                                current_kwargs.pop("data", None)
                                current_kwargs.pop("json", None)
                            continue

                    break

                if res.status_code == 404:
                    logger.debug("Resource not found (HTTP 404) at %s", clean_log_url)
                    raise NotFoundError(f"Resource not found at {clean_log_url}", url=current_url)

                if res.status_code == 429:
                    retry_header = res.headers.get("retry-after") or res.headers.get("Retry-After")
                    try:
                        retry_after = float(retry_header) if retry_header else backoff
                    except (ValueError, TypeError):
                        retry_after = backoff

                    if attempts < self.max_retries:
                        logger.warning("Rate limit 429 hit at %s. Retrying in %.1fs (attempt %d/%d)...", clean_log_url, retry_after, attempts, self.max_retries)
                        await asyncio.sleep(retry_after)
                        backoff *= 2.0
                        continue

                    raise RateLimitError(
                        f"Rate limit exceeded (HTTP 429) at {clean_log_url}",
                        retry_after=retry_after,
                        url=current_url,
                    )

                if res.status_code in (401, 403):
                    try:
                        data = res.json()
                        self._check_json_errors(data, url=current_url)
                    except (CheckpointError, ActionBlockedError, LoginRequiredError):
                        raise
                    except Exception:
                        pass
                    logger.warning("Permission denied (HTTP %d) at %s", res.status_code, clean_log_url)
                    raise PermissionDeniedError(f"Permission denied (HTTP {res.status_code}) at {clean_log_url}", status_code=res.status_code, url=current_url)

                if res.status_code >= 500:
                    if attempts < self.max_retries:
                        logger.warning("Server error HTTP %d at %s. Retrying in %.1fs (attempt %d/%d)...", res.status_code, clean_log_url, backoff, attempts, self.max_retries)
                        await asyncio.sleep(backoff)
                        backoff *= 2.0
                        continue
                    raise ServerError(f"Server error HTTP {res.status_code} at {clean_log_url}", status_code=res.status_code, url=current_url)

                if res.status_code >= 400:
                    try:
                        data = res.json()
                        self._check_json_errors(data, url=current_url)
                    except (CheckpointError, ActionBlockedError, LoginRequiredError):
                        raise
                    except Exception:
                        pass
                    raise BadRequestError(f"Request failed with HTTP {res.status_code} at {clean_log_url}", status_code=res.status_code, url=current_url)

                data = res.json()
                self._check_json_errors(data, url=current_url)
                return data

            except (
                LoginRequiredError,
                CheckpointError,
                ActionBlockedError,
                RateLimitError,
                NotFoundError,
                BadRequestError,
                ServerError,
                PermissionDeniedError,
                GraphQLQueryError,
            ):
                raise
            except Exception as e:
                is_timeout = "timeout" in str(e).lower() or "timed out" in str(e).lower()
                if attempts >= self.max_retries:
                    if is_timeout:
                        raise ConnectionTimeoutError(f"Request to {clean_log_url} timed out: {e}") from e
                    raise NetworkConnectionError(f"Request to {clean_log_url} failed: {e}") from e

                logger.debug("Network/transport attempt %d failed for %s: %s. Retrying...", attempts, clean_log_url, e)
                await asyncio.sleep(1.5)

        return {}

    async def graphql_api(
        self,
        doc_id: str,
        variables: dict[str, Any],
        referer: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute a modern Polaris Relay GraphQL query via /api/graphql/.

        :param doc_id: Numeric document identifier.
        :type doc_id: str
        :param variables: Query variables dictionary.
        :type variables: dict[str, Any]
        :param referer: Optional HTTP Referer header URL.
        :type referer: str | None
        :return: JSON response dictionary.
        :rtype: dict[str, Any]
        """
        if not self._bootstrapped:
            await self._bootstrap_session()

        headers = {
            "X-FB-LSD": self.lsd_token or "",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
        }
        if referer:
            headers["Referer"] = referer

        payload = {
            "doc_id": doc_id,
            "variables": json.dumps(variables),
            "lsd": self.lsd_token or "",
        }

        return await self._request("POST", "https://www.instagram.com/api/graphql/", headers=headers, data=payload)

    async def graphql_query(
        self,
        query_hash: str | None = None,
        doc_id: str | None = None,
        variables: dict[str, Any] | None = None,
        referer: str | None = None,
        method: str = "GET",
    ) -> dict[str, Any]:
        """
        Execute a traditional GraphQL query via the legacy /graphql/query/ endpoint.

        :param query_hash: 32-character MD5 query hash.
        :type query_hash: str | None
        :param doc_id: Numeric document identifier.
        :type doc_id: str | None
        :param variables: Variables mapping for the query.
        :type variables: dict[str, Any] | None
        :param referer: Optional HTTP Referer URL.
        :type referer: str | None
        :param method: HTTP method ('GET' or 'POST').
        :type method: str
        :return: JSON response dictionary.
        :rtype: dict[str, Any]
        """
        if not self._bootstrapped:
            await self._bootstrap_session()

        headers: dict[str, str] = {
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            headers["Referer"] = referer

        url = "https://www.instagram.com/graphql/query/"
        var_payload = json.dumps(variables or {})

        if method.upper() == "POST":
            form: dict[str, Any] = {"variables": var_payload, "server_timestamps": "true"}
            if doc_id:
                form["doc_id"] = doc_id
            if query_hash:
                form["query_hash"] = query_hash
            return await self._request("POST", url, headers=headers, data=form)

        params: dict[str, Any] = {"variables": var_payload}
        if query_hash:
            params["query_hash"] = query_hash
        if doc_id:
            params["doc_id"] = doc_id
        return await self._request("GET", url, headers=headers, params=params)

    async def get_profile(self, username: str) -> Profile:
        """
        Fetch public or authenticated user profile details.

        :param username: Target Instagram username.
        :type username: str
        :return: Profile domain model.
        :rtype: Profile
        :raises NotFoundError: If the target user profile does not exist.
        :raises LoginRequiredError: If the profile is restricted or private without session.
        """
        clean_user = username.lower().strip("@")
        cache_key = f"profile:{clean_user}"

        if self.cache:
            cached = await self.cache.get_api(cache_key)
            if cached:
                logger.debug("Cache hit for profile '%s'", clean_user)
                return Profile.from_api_dict(cached)

        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={clean_user}"
        try:
            data = await self._request("GET", url)
            user_data = (data.get("data") or {}).get("user", {})
            if user_data:
                if self.cache:
                    await self.cache.set_api(cache_key, user_data, ttl_seconds=43200)
                return Profile.from_api_dict(user_data)
        except (RateLimitError, CheckpointError, ActionBlockedError):
            raise
        except (BadRequestError, NotFoundError, PermissionDeniedError, LoginRequiredError) as e:
            logger.debug("Web profile lookup for '%s' returned %s, trying mobile endpoint fallback...", clean_user, e)

        mobile_url = f"https://www.instagram.com/api/v1/feed/user/{clean_user}/username/?count=1"
        try:
            data = await self._request("GET", mobile_url)
            user_data = data.get("user")
            if user_data and isinstance(user_data, dict):
                if self.cache:
                    await self.cache.set_api(cache_key, user_data, ttl_seconds=43200)
                return Profile.from_api_dict(user_data)
        except (RateLimitError, CheckpointError, ActionBlockedError):
            raise
        except Exception as e:
            logger.debug("Mobile profile lookup for '%s' failed: %s", clean_user, e)

        if not self.is_authenticated:
            raise LoginRequiredError(f"Profile '{username}' requires authentication or is restricted", url=url)

        raise NotFoundError(f"Target user '{username}' could not be resolved", url=url)

    async def get_user_id(self, username: str) -> str:
        """
        Resolve the numeric user ID for a target username.

        :param username: Target username.
        :type username: str
        :return: Numeric user ID string.
        :rtype: str
        """
        clean_user = username.lower().strip("@")
        cache_key = f"user_id:{clean_user}"

        if self.cache:
            cached = await self.cache.get_api(cache_key)
            if cached and "id" in cached:
                return str(cached["id"])

        profile = await self.get_profile(clean_user)
        if not profile.id:
            raise NotFoundError(f"User ID for '{username}' could not be resolved")

        if self.cache:
            await self.cache.set_api(cache_key, {"id": profile.id}, ttl_seconds=2592000)
        return profile.id

    async def get_username_by_user_id(self, user_id: str) -> str:
        """
        Resolve the username from a numeric user ID.

        :param user_id: Numeric user identifier.
        :type user_id: str
        :return: Username string.
        :rtype: str
        """
        cache_key = f"username:{user_id}"
        if self.cache:
            cached = await self.cache.get_api(cache_key)
            if cached and "username" in cached:
                return str(cached["username"])

        url = f"https://www.instagram.com/api/v1/users/{user_id}/info/"
        data = await self._request("GET", url)
        username = (data.get("user") or {}).get("username")
        if not username:
            raise NotFoundError(f"Username for ID '{user_id}' could not be resolved", url=url)

        if self.cache:
            await self.cache.set_api(cache_key, {"username": username}, ttl_seconds=2592000)
        return str(username)

    async def get_user_hd_profile_pic(self, user_id: str, username: str = "") -> str:
        """
        Fetch the true high-definition profile picture URL for the target user.

        :param user_id: Target numeric user ID.
        :type user_id: str
        :param username: Target username.
        :type username: str
        :return: High-definition profile picture URL string.
        :rtype: str
        """
        clean_user = username.lower().strip("@") if username else ""

        if user_id:
            try:
                info_url = f"https://www.instagram.com/api/v1/users/{user_id}/info/"
                data = await self._request("GET", info_url)
                info_user = data.get("user")
                if isinstance(info_user, dict):
                    pic = extract_hd_profile_pic_url(info_user, allow_standard_fallback=False)
                    if pic:
                        return pic
            except (CheckpointError, ActionBlockedError, RateLimitError):
                raise
            except Exception as e:
                logger.debug("HD avatar user info lookup failed for user_id '%s': %s", user_id, e)

        if clean_user:
            try:
                web_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={clean_user}"
                data = await self._request("GET", web_url)
                web_user = (data.get("data") or {}).get("user", {})
                if isinstance(web_user, dict):
                    pic = extract_hd_profile_pic_url(web_user, allow_standard_fallback=False)
                    if pic:
                        return pic
            except (CheckpointError, ActionBlockedError, RateLimitError):
                raise
            except Exception as e:
                logger.debug("HD avatar web profile lookup failed for '%s': %s", clean_user, e)

        feed_endpoints = []
        if user_id:
            feed_endpoints.append(f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count=1")
        if clean_user:
            feed_endpoints.append(f"https://www.instagram.com/api/v1/feed/user/{clean_user}/username/?count=1")

        for feed_url in feed_endpoints:
            try:
                data = await self._request("GET", feed_url)
                items = data.get("items") or []
                if items and isinstance(items[0], dict):
                    item_user = items[0].get("user") or items[0].get("owner")
                    if isinstance(item_user, dict):
                        pic = extract_hd_profile_pic_url(item_user, allow_standard_fallback=False)
                        if pic:
                            return pic

                feed_user = data.get("user")
                if isinstance(feed_user, dict):
                    pic = extract_hd_profile_pic_url(feed_user, allow_standard_fallback=False)
                    if pic:
                        return pic
            except (CheckpointError, ActionBlockedError, RateLimitError):
                raise
            except Exception as e:
                logger.debug("HD avatar feed extraction failed for URL '%s': %s", feed_url, e)

        if clean_user:
            try:
                profile = await self.get_profile(clean_user)
                if profile.profile_pic_url:
                    return profile.profile_pic_url
            except (CheckpointError, ActionBlockedError, RateLimitError):
                raise
            except Exception as e:
                logger.debug("HD avatar profile fallback failed for '%s': %s", clean_user, e)

        return ""

    def iter_posts(
        self,
        user_id: str,
        limit: int = 0,
        since: datetime | None = None,
        state: PaginationState | None = None,
    ) -> AsyncNodeIterator[MediaItem]:
        """
        Lazily paginate and yield regular posts published by the user.

        :param user_id: Numeric user ID.
        :type user_id: str
        :param limit: Maximum number of items to yield (0 for unlimited).
        :type limit: int
        :param since: Optional UTC datetime cutoff to stop pagination early.
        :type since: datetime | None
        :param state: Optional previous PaginationState to resume from.
        :type state: PaginationState | None
        :return: Resumable AsyncNodeIterator yielding :class:`MediaItem` instances.
        :rtype: AsyncNodeIterator[MediaItem]
        """
        async def _fetch_page(cursor: str | None) -> PageResult[MediaItem]:
            url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count=33" + (f"&max_id={cursor}" if cursor else "")
            data = await self._request("GET", url)
            raw_items = data.get("items") or []
            filtered = [
                MediaItem.from_api_dict(raw, content_type="posts")
                for raw in raw_items
                if raw.get("product_type") != "clips"
            ]
            next_max_id = str(data.get("next_max_id", ""))
            has_more = bool(data.get("more_available", False)) and bool(next_max_id)
            return PageResult(items=filtered, next_cursor=next_max_id if has_more else None, has_more=has_more)

        return AsyncNodeIterator(fetch_page=_fetch_page, limit=limit, since=since, state=state)

    async def _get_logged_out_media(self, media_id: str) -> dict[str, Any]:
        """
        Query detailed metadata for a single media item via Polaris logged-out GraphQL endpoint.

        :param media_id: Numeric Instagram media identifier.
        :type media_id: str
        :return: Extracted media node dictionary.
        :rtype: dict[str, Any]
        """
        variables = {"media_id": str(media_id)}
        try:
            res = await self.graphql_api(DOC_ID_POLARIS_MEDIA, variables)
            media_data = (res.get("data") or {}).get("xig_polaris_media") or {}
            if isinstance(media_data, dict):
                inner = media_data.get("if_not_gated_logged_out")
                return inner if isinstance(inner, dict) else media_data
        except Exception as e:
            logger.debug("Logged-out media detail query failed for media_id '%s': %s", media_id, e)
        return {}

    def iter_reels(
        self,
        user_id: str,
        username: str = "",
        limit: int = 0,
        since: datetime | None = None,
        state: PaginationState | None = None,
    ) -> AsyncNodeIterator[MediaItem]:
        """
        Lazily paginate and yield video Reels published by the user.

        :param user_id: Numeric user ID.
        :type user_id: str
        :param username: Target username (required for anonymous reel queries).
        :type username: str
        :param limit: Maximum number of items to yield (0 for unlimited).
        :type limit: int
        :param since: Optional UTC datetime cutoff to stop pagination early.
        :type since: datetime | None
        :param state: Optional previous PaginationState to resume from.
        :type state: PaginationState | None
        :return: Resumable AsyncNodeIterator yielding :class:`MediaItem` instances.
        :rtype: AsyncNodeIterator[MediaItem]
        """
        if not self.is_authenticated and username:
            async def _fetch_anonymous_reels(cursor: str | None) -> PageResult[MediaItem]:
                referer = f"https://www.instagram.com/{username}/reels/"
                variables: dict[str, Any] = {"username": username, "first": 12}
                if cursor:
                    variables["after"] = cursor

                data = await self.graphql_api(DOC_ID_POLARIS_CLIPS, variables, referer=referer)
                clips_conn = (
                    (data.get("data") or {})
                    .get("xig_user_by_username", {})
                    .get("polaris_clips_connection", {})
                )
                edges = clips_conn.get("edges") or []
                items: list[MediaItem] = []
                for edge in edges:
                    node = edge.get("node") if isinstance(edge, dict) and "node" in edge else edge
                    if not isinstance(node, dict):
                        continue
                    pk = str(node.get("pk") or node.get("id") or "").replace("POLARIS_", "")
                    detail = await self._get_logged_out_media(pk)
                    merged = {**node, **detail}
                    items.append(MediaItem.from_api_dict(merged, content_type="reels"))

                page_info = clips_conn.get("page_info") or {}
                has_more = bool(page_info.get("has_next_page", False))
                next_cursor = str(page_info.get("end_cursor", "")) if has_more else None
                return PageResult(items=items, next_cursor=next_cursor, has_more=has_more and bool(next_cursor))

            return AsyncNodeIterator(fetch_page=_fetch_anonymous_reels, limit=limit, since=since, state=state)

        async def _fetch_auth_reels(cursor: str | None) -> PageResult[MediaItem]:
            form_data = {"target_user_id": user_id, "page_size": 50, "include_feed_video": "true"}
            if cursor:
                form_data["max_id"] = cursor

            data = await self._request("POST", "https://www.instagram.com/api/v1/clips/user/", data=form_data)
            items_raw = data.get("items") or []
            items: list[MediaItem] = []
            for entry in items_raw:
                media = entry.get("media")
                if isinstance(media, dict):
                    items.append(MediaItem.from_api_dict(media, content_type="reels"))

            paging = data.get("paging_info") or {}
            max_id = str(paging.get("max_id", ""))
            has_more = bool(paging.get("more_available", False)) and bool(max_id)
            return PageResult(items=items, next_cursor=max_id if has_more else None, has_more=has_more)

        return AsyncNodeIterator(fetch_page=_fetch_auth_reels, limit=limit, since=since, state=state)

    async def iter_stories(self, user_id: str) -> AsyncIterator[MediaItem]:
        """
        Fetch and yield active ephemeral stories.

        :param user_id: Numeric user ID.
        :type user_id: str
        :return: AsyncIterator yielding :class:`MediaItem` instances.
        :rtype: AsyncIterator[MediaItem]
        """
        items: list[dict[str, Any]] = []

        try:
            data = await self._request("GET", f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}")
            reels = data.get("reels") or {}
            user_reel = reels.get(str(user_id)) or {}
            items = user_reel.get("items") or []
        except (LoginRequiredError, CheckpointError, ActionBlockedError, RateLimitError):
            raise
        except Exception as e:
            logger.debug("reels_media stories query failed for %s: %s", user_id, e)

        if not items:
            try:
                data = await self._request("GET", f"https://www.instagram.com/api/v1/feed/user/{user_id}/story/")
                reel = data.get("reel") or {}
                items = reel.get("items") or []
            except (LoginRequiredError, CheckpointError, ActionBlockedError, RateLimitError):
                raise
            except Exception as e:
                logger.debug("user/story endpoint failed for %s: %s", user_id, e)

        for raw in items:
            yield MediaItem.from_api_dict(raw, content_type="stories")

    async def iter_highlights(self, user_id: str) -> AsyncIterator[HighlightGroup]:
        """
        Fetch and yield story highlight groups with their contained items.

        :param user_id: Numeric user ID.
        :type user_id: str
        :return: AsyncIterator yielding :class:`HighlightGroup` instances.
        :rtype: AsyncIterator[HighlightGroup]
        """
        tray_url = f"https://www.instagram.com/api/v1/highlights/{user_id}/highlights_tray/"
        try:
            data = await self._request("GET", tray_url)
        except (LoginRequiredError, CheckpointError, ActionBlockedError, RateLimitError):
            raise
        except Exception as e:
            logger.debug("Highlights tray request failed for user %s: %s", user_id, e)
            return

        tray = data.get("tray") or []
        for hl in tray:
            if not isinstance(hl, dict):
                continue
            hid = str(hl.get("id", "")).replace("highlight:", "")
            title = str(hl.get("title") or f"Highlight_{hid}")
            cover_url = (hl.get("cover_media") or {}).get("cropped_image_version", {}).get("url", "")

            try:
                stories_data = await self._request(
                    "GET",
                    f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids=highlight:{hid}",
                )
                reels_map = stories_data.get("reels") or {}
                reel = (
                    reels_map.get(f"highlight:{hid}")
                    or reels_map.get(hid)
                    or (next(iter(reels_map.values())) if reels_map else {})
                ) or {}
                raw_items = reel.get("items") or []
                items = [MediaItem.from_api_dict(it, content_type="highlights") for it in raw_items]
                yield HighlightGroup(id=hid, title=title, cover_url=cover_url, items=items)
            except (LoginRequiredError, CheckpointError, ActionBlockedError, RateLimitError):
                raise
            except Exception as e:
                logger.debug("Failed to fetch highlight stories for group '%s' (%s): %s", title, hid, e)
                continue

    def iter_tagged(
        self,
        user_id: str,
        limit: int = 0,
        since: datetime | None = None,
        state: PaginationState | None = None,
    ) -> AsyncNodeIterator[MediaItem]:
        """
        Lazily paginate and yield posts where the user is tagged.

        :param user_id: Numeric user ID.
        :type user_id: str
        :param limit: Maximum number of items to yield (0 for unlimited).
        :type limit: int
        :param since: Optional UTC datetime cutoff to stop pagination early.
        :type since: datetime | None
        :param state: Optional previous PaginationState to resume from.
        :type state: PaginationState | None
        :return: Resumable AsyncNodeIterator yielding :class:`MediaItem` instances.
        :rtype: AsyncNodeIterator[MediaItem]
        """
        async def _fetch_tagged_page(cursor: str | None) -> PageResult[MediaItem]:
            url = f"https://www.instagram.com/api/v1/usertags/{user_id}/feed/" + (f"?max_id={cursor}" if cursor else "")
            data = await self._request("GET", url)
            raw_items = data.get("items") or []
            items = [MediaItem.from_api_dict(raw, content_type="tagged") for raw in raw_items]
            next_max_id = str(data.get("next_max_id", ""))
            has_more = bool(data.get("more_available", False)) and bool(next_max_id)
            return PageResult(items=items, next_cursor=next_max_id if has_more else None, has_more=has_more)

        return AsyncNodeIterator(fetch_page=_fetch_tagged_page, limit=limit, since=since, state=state)