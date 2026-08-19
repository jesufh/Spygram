"""
spygram.auth
~~~~~~~~~~~~

Authentication and session persistence helpers.

Provides clean functions to save and load Instagram session credentials
from JSON files and extract live cookies from installed desktop browsers
without direct terminal or UI coupling.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from spygram.config import get_default_config_dir
from spygram.exceptions import InvalidSessionError

logger = logging.getLogger("spygram.auth")

REQUIRED_COOKIES = ("sessionid",)
SUPPORTED_BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "chromium", "vivaldi", "safari")


def get_sessions_dir(base_config_dir: Path | None = None) -> Path:
    """
    Resolve the directory where session JSON files are stored.

    :param base_config_dir: Optional custom config directory.
    :type base_config_dir: Path | None
    :return: Sessions directory path.
    :rtype: Path
    """
    base = base_config_dir or get_default_config_dir()
    sessions_path = base / "sessions"
    sessions_path.mkdir(parents=True, exist_ok=True)
    return sessions_path


def get_session_file_path(username: str, sessions_dir: Path | None = None) -> Path:
    """
    Get the full file path for a specific user's session file.

    :param username: Target account username.
    :type username: str
    :param sessions_dir: Optional custom sessions directory.
    :type sessions_dir: Path | None
    :return: Path to the JSON session file.
    :rtype: Path
    """
    clean_username = username.lower().strip("@")
    target_dir = sessions_dir or get_sessions_dir()
    return target_dir / f"{clean_username}_session.json"


def save_session(cookies: dict[str, str], username: str, sessions_dir: Path | None = None) -> Path:
    """
    Persist Instagram session cookies to a local JSON file with restricted permissions.

    :param cookies: Mapping of cookie names to string values.
    :type cookies: dict[str, str]
    :param username: Associated account username.
    :type username: str
    :param sessions_dir: Optional custom sessions directory.
    :type sessions_dir: Path | None
    :return: Path where the session file was written.
    :rtype: Path
    :raises InvalidSessionError: If required session cookies are missing.
    """
    if not any(cookies.get(k) for k in REQUIRED_COOKIES):
        raise InvalidSessionError(f"Cannot save session: missing required cookies ({REQUIRED_COOKIES})")

    file_path = get_session_file_path(username, sessions_dir)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")

    if os.name == "posix":
        try:
            os.chmod(file_path, 0o600)
        except OSError as e:
            logger.debug("Could not set 0600 permissions on %s: %s", file_path, e)

    logger.debug("Saved session for user '%s' at %s", username, file_path)
    return file_path


def load_session(username: str, sessions_dir: Path | None = None) -> dict[str, str] | None:
    """
    Load and validate a saved session JSON file for a given username.

    :param username: Account username.
    :type username: str
    :param sessions_dir: Optional custom sessions directory.
    :type sessions_dir: Path | None
    :return: Cookie mapping if valid, None if not found or invalid.
    :rtype: dict[str, str] | None
    """
    file_path = get_session_file_path(username, sessions_dir)
    if not file_path.is_file():
        logger.debug("Session file not found at %s", file_path)
        return None

    try:
        data: Any = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("Session file %s does not contain a JSON dictionary", file_path)
            return None
        cookie_dict = {str(k): str(v) for k, v in data.items()}
        if not all(cookie_dict.get(k) for k in REQUIRED_COOKIES):
            logger.warning("Session file %s missing required cookie 'sessionid'", file_path)
            return None
        return cookie_dict
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse session file %s: %s", file_path, e)
        return None


def list_saved_sessions(sessions_dir: Path | None = None) -> list[str]:
    """
    List all available saved session usernames.

    :param sessions_dir: Optional custom sessions directory.
    :type sessions_dir: Path | None
    :return: Sorted list of usernames with stored session files.
    :rtype: list[str]
    """
    target_dir = sessions_dir or get_sessions_dir()
    if not target_dir.is_dir():
        return []

    usernames: list[str] = []
    for path in target_dir.glob("*_session.json"):
        if path.is_file():
            uname = path.name.replace("_session.json", "")
            if uname:
                usernames.append(uname)
    return sorted(usernames)


def extract_user_id_from_session(cookies: dict[str, str]) -> str | None:
    """
    Extract the account user ID from the ``sessionid`` or ``ds_user_id`` cookie.

    :param cookies: Mapping of cookie names to values.
    :type cookies: dict[str, str]
    :return: Extracted numeric user ID string, or None if unavailable.
    :rtype: str | None
    """
    if ds_user_id := cookies.get("ds_user_id"):
        return ds_user_id.strip()

    if sessionid := cookies.get("sessionid"):
        if "%3A" in sessionid:
            return sessionid.split("%3A")[0].strip()
        if ":" in sessionid:
            return sessionid.split(":")[0].strip()

    return None


def extract_browser_cookies(browser_name: str | None = None) -> dict[str, str] | None:
    """
    Extract Instagram authentication cookies from installed desktop web browsers.

    Uses :mod:`rookiepy` to decrypt browser cookie stores safely.

    :param browser_name: Name of a specific browser, or None to scan all supported browsers.
    :type browser_name: str | None
    :return: Extracted valid cookie dictionary, or None if not found.
    :rtype: dict[str, str] | None
    """
    try:
        import rookiepy
    except ImportError:
        logger.debug("rookiepy library not installed; browser cookie extraction unavailable")
        return None

    targets = [browser_name.lower()] if browser_name else list(SUPPORTED_BROWSERS)

    for browser in targets:
        if not hasattr(rookiepy, browser):
            continue
        try:
            extractor = getattr(rookiepy, browser)
            raw_cookies = extractor(domains=[".instagram.com"])
            cookie_dict = {
                c["name"]: c["value"]
                for c in raw_cookies
                if isinstance(c, dict) and "name" in c and "value" in c
            }
            if all(cookie_dict.get(k) for k in REQUIRED_COOKIES):
                logger.debug("Successfully extracted Instagram session cookies from browser '%s'", browser)
                return cookie_dict
        except Exception as e:
            logger.debug("Cookie extraction from browser '%s' failed: %s", browser, e)
            continue

    logger.debug("No active Instagram session cookies extracted from browsers: %s", targets)
    return None