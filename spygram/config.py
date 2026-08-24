"""
spygram.config
~~~~~~~~~~~~~~

Global configuration management and filesystem path resolution for Spygram.

Uses standard operating system locations via :mod:`platformdirs` to store
persistent session credentials and database caches safely across executions,
while keeping downloads in a configurable destination directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import platformdirs

APP_NAME = "spygram"
"""Application identifier name."""

APP_AUTHOR = "spygram"
"""Application vendor/author identifier."""

INSTAGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
"""Regular expression pattern for valid Instagram account handles."""


def validate_username(username: str) -> str:
    """
    Validate and normalize an Instagram username handle to prevent path traversal.

    :param username: Target Instagram username.
    :type username: str
    :return: Normalized lowercase username.
    :rtype: str
    :raises ValueError: If username format is invalid or contains path separators.
    """
    clean = username.lower().strip("@").strip()
    if not INSTAGRAM_USERNAME_RE.match(clean):
        raise ValueError(f"invalid Instagram username format: {username!r}")
    return clean


def get_default_config_dir() -> Path:
    """
    Get the standard operating system directory for configuration and saved sessions.

    :return: Path to the user configuration directory.
    :rtype: Path
    """
    path = Path(platformdirs.user_config_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_cache_dir() -> Path:
    """
    Get the standard operating system directory for database cache files.

    :return: Path to the user cache directory.
    :rtype: Path
    """
    path = Path(platformdirs.user_cache_dir(APP_NAME, APP_AUTHOR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_downloads_dir() -> Path:
    """
    Get the default directory for downloaded media files.

    :return: Path to the local downloads directory.
    :rtype: Path
    """
    path = Path.cwd() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(slots=True, frozen=True)
class AppConfig:
    """
    Immutable application settings container.

    :param config_dir: Directory where sessions and credentials are stored.
    :type config_dir: Path
    :param cache_dir: Directory where the SQLite cache database is stored.
    :type cache_dir: Path
    :param downloads_dir: Base directory where scraped media will be organized.
    :type downloads_dir: Path
    :param max_retries: Maximum number of HTTP retry attempts for transient errors.
    :type max_retries: int
    :param request_timeout: Timeout in seconds for standard API requests.
    :type request_timeout: float
    :param download_timeout: Timeout in seconds for binary media streaming.
    :type download_timeout: float
    :param max_concurrent_downloads: Semaphore limit for concurrent media downloads.
    :type max_concurrent_downloads: int
    """

    config_dir: Path = field(default_factory=get_default_config_dir)
    cache_dir: Path = field(default_factory=get_default_cache_dir)
    downloads_dir: Path = field(default_factory=get_default_downloads_dir)
    max_retries: int = 3
    request_timeout: float = 30.0
    download_timeout: float = 120.0
    max_concurrent_downloads: int = 3

    @property
    def cache_db_path(self) -> Path:
        """
        Path to the primary SQLite cache database file.

        :return: Path to cache database.
        :rtype: Path
        """
        return self.cache_dir / "spygram_cache.db"

    @property
    def sessions_dir(self) -> Path:
        """
        Directory where JSON session files are located.

        :return: Path to sessions folder.
        :rtype: Path
        """
        sessions_path = self.config_dir / "sessions"
        sessions_path.mkdir(parents=True, exist_ok=True)
        return sessions_path

    def get_user_downloads_dir(self, username: str) -> Path:
        """
        Resolve and ensure the target user's root download directory.

        :param username: Target Instagram username.
        :type username: str
        :return: Directory path for the specific user.
        :rtype: Path
        """
        clean_user = validate_username(username)
        target_dir = self.downloads_dir / clean_user
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def get_content_downloads_dir(self, username: str, content_type: str) -> Path:
        """
        Resolve and ensure a subdirectory for a specific content type.

        :param username: Target Instagram username.
        :type username: str
        :param content_type: Category name (e.g., 'posts', 'stories', 'reels', 'highlights', 'tagged').
        :type content_type: str
        :return: Directory path for the specific content type.
        :rtype: Path
        """
        content_dir = self.get_user_downloads_dir(username) / content_type
        content_dir.mkdir(parents=True, exist_ok=True)
        return content_dir