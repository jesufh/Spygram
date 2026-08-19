"""
spygram.cache
~~~~~~~~~~~~~

High-performance SQLite caching manager for API responses and downloaded media.

Maintains a persistent connection operating in Write-Ahead Logging (WAL)
mode with thread synchronization, resilient error handling, automatic
corrupted record eviction, and diagnostic logging.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

from spygram.exceptions import (
    CacheCorruptedError,
    CacheError,
    DatabaseLockError,
)

logger = logging.getLogger("spygram.cache")


class Cache:
    """
    Thread-safe, asynchronous SQLite cache repository.

    :param db_path: Filesystem path to the SQLite database file.
    :type db_path: Path
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> Cache:
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.close()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get or initialize the persistent SQLite connection with WAL mode enabled.

        :return: Open SQLite connection.
        :rtype: sqlite3.Connection
        :raises CacheError: If database initialization or file access fails.
        """
        if self._conn is None:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(
                    str(self.db_path),
                    check_same_thread=False,
                    timeout=30.0,
                )
                self._conn.execute("PRAGMA journal_mode=WAL;")
                self._conn.execute("PRAGMA synchronous=NORMAL;")
            except sqlite3.OperationalError as e:
                logger.error("Failed to open SQLite database at %s (locked/busy): %s", self.db_path, e)
                raise DatabaseLockError(f"Database at {self.db_path} is locked: {e}") from e
            except sqlite3.Error as e:
                logger.error("Failed to initialize SQLite connection at %s: %s", self.db_path, e)
                raise CacheError(f"Database connection error at {self.db_path}: {e}") from e
        return self._conn

    def _init_db(self) -> None:
        """
        Initialize required database schemas for API and media tracking.
        """
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS api_cache (
                        key TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS media_cache (
                        media_id TEXT PRIMARY KEY,
                        file_path TEXT NOT NULL,
                        file_size INTEGER NOT NULL,
                        downloaded_at REAL NOT NULL
                    );
                    """
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.error("Error creating database schemas at %s: %s", self.db_path, e)
                raise CacheError(f"Failed to initialize database schema: {e}") from e

    def _sync_get_api(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.execute(
                    "SELECT payload FROM api_cache WHERE key = ? AND expires_at > ?",
                    (key, time.time()),
                )
                row = cursor.fetchone()
                if row:
                    try:
                        return json.loads(row[0])
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("Corrupted cache payload for key '%s' (%s). Evicting record.", key, e)
                        conn.execute("DELETE FROM api_cache WHERE key = ?", (key,))
                        conn.commit()
                        return None
                return None
            except sqlite3.Error as e:
                logger.debug("Cache read failed for key '%s': %s", key, e)
                return None

    def _sync_set_api(self, key: str, data: dict[str, Any], ttl_seconds: float) -> None:
        try:
            payload = json.dumps(data, ensure_ascii=False)
            expires_at = time.time() + ttl_seconds
            with self._lock:
                conn = self._get_connection()
                conn.execute(
                    "INSERT OR REPLACE INTO api_cache (key, payload, expires_at) VALUES (?, ?, ?)",
                    (key, payload, expires_at),
                )
                conn.commit()
        except (TypeError, ValueError) as e:
            logger.warning("Cannot serialize cache data for key '%s': %s", key, e)
        except sqlite3.Error as e:
            logger.debug("Cache write failed for key '%s': %s", key, e)

    def _sync_is_media_downloaded(self, media_id: str, dest_path: str | None = None) -> bool:
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.execute(
                    "SELECT file_path, file_size FROM media_cache WHERE media_id = ?",
                    (media_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return False

                cached_path, expected_size = row[0], row[1]
                check_path = Path(dest_path or cached_path)

                if check_path.is_file() and check_path.stat().st_size == expected_size and expected_size > 0:
                    return True

                return False
            except sqlite3.Error as e:
                logger.debug("Media cache verification failed for '%s': %s", media_id, e)
                return False
            except OSError as e:
                logger.debug("Filesystem check failed during media cache check for '%s': %s", media_id, e)
                return False

    def _sync_register_media(self, media_id: str, file_path: str, file_size: int) -> None:
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute(
                    "INSERT OR REPLACE INTO media_cache (media_id, file_path, file_size, downloaded_at) VALUES (?, ?, ?, ?)",
                    (media_id, str(Path(file_path).resolve().as_posix()), file_size, time.time()),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.debug("Register media failed for '%s': %s", media_id, e)

    def _sync_clear_expired(self) -> None:
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute("DELETE FROM api_cache WHERE expires_at <= ?", (time.time(),))
                conn.commit()
            except sqlite3.Error as e:
                logger.debug("Clear expired cache failed: %s", e)

    def _sync_clear_all(self) -> None:
        with self._lock:
            try:
                conn = self._get_connection()
                conn.execute("DELETE FROM api_cache")
                conn.execute("DELETE FROM media_cache")
                conn.commit()
            except sqlite3.Error as e:
                logger.error("Clear all cache failed: %s", e)
                raise CacheError(f"Failed to clear cache: {e}") from e

    async def get_api(self, key: str) -> dict[str, Any] | None:
        """
        Retrieve a non-expired cached API response dictionary.

        :param key: Cache identifier key.
        :type key: str
        :return: Cached dictionary, or None if expired or not found.
        :rtype: dict[str, Any] | None
        """
        return await asyncio.to_thread(self._sync_get_api, key)

    async def set_api(self, key: str, data: dict[str, Any], ttl_seconds: float) -> None:
        """
        Store an API response dictionary with a time-to-live expiration.

        :param key: Cache identifier key.
        :type key: str
        :param data: JSON-serializable dictionary to cache.
        :type data: dict[str, Any]
        :param ttl_seconds: Expiration lifespan in seconds.
        :type ttl_seconds: float
        """
        await asyncio.to_thread(self._sync_set_api, key, data, ttl_seconds)

    async def is_media_downloaded(self, media_id: str, dest_path: str | None = None) -> bool:
        """
        Verify if a media item is already present in cache and on disk with valid file size.

        :param media_id: Unique identifier for the media resource.
        :type media_id: str
        :param dest_path: Optional explicit filesystem path to inspect.
        :type dest_path: str | None
        :return: True if the file exists on disk and matches recorded size.
        :rtype: bool
        """
        return await asyncio.to_thread(self._sync_is_media_downloaded, media_id, dest_path)

    async def register_media(self, media_id: str, file_path: str, file_size: int) -> None:
        """
        Record a successfully downloaded media file into the cache index.

        :param media_id: Unique identifier for the media resource.
        :type media_id: str
        :param file_path: Path where the file was saved.
        :type file_path: str
        :param file_size: Total file size in bytes.
        :type file_size: int
        """
        await asyncio.to_thread(self._sync_register_media, media_id, file_path, file_size)

    async def clear_expired(self) -> None:
        """
        Purge all expired API entries from the database.
        """
        await asyncio.to_thread(self._sync_clear_expired)

    async def clear_all(self) -> None:
        """
        Completely truncate all API and media cache tables.
        """
        await asyncio.to_thread(self._sync_clear_all)

    def close(self) -> None:
        """
        Commit outstanding transactions and cleanly close the database connection.
        """
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.commit()
                except sqlite3.Error:
                    pass
                finally:
                    self._conn.close()
                    self._conn = None