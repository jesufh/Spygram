"""Configuration and constants for Spygram."""

from pathlib import Path
import random

BASE_DIR = Path(__file__).parent.parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
SESSIONS_DIR = BASE_DIR / ".sessions"


def get_user_dir(username: str) -> Path:
    """Get the download directory for a specific user."""
    return DOWNLOADS_DIR / username


def get_content_dir(username: str, content_type: str) -> Path:
    """Get subdirectory for specific content type (e.g. posts, stories)."""
    d = get_user_dir(username) / content_type
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_sessions_dir() -> Path:
    """Ensure sessions directory exists."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR


MIN_DELAY = 2.0
MAX_DELAY = 5.0
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0
CHUNK_SIZE = 1024 * 1024

DEFAULT_POSTS_LIMIT = 0
DEFAULT_REELS_LIMIT = 0
DEFAULT_TAGGED_LIMIT = 0


def random_delay() -> float:
    """Return a random delay between MIN and MAX."""
    return random.uniform(MIN_DELAY, MAX_DELAY)
