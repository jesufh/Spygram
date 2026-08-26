"""
spygram.downloader
~~~~~~~~~~~~~~~~~~

High-performance, atomic media download service.

Handles concurrent streaming of photos and videos with atomic disk writing,
multi-tier cache deduplication, event reporting callbacks, error tracking,
and metadata index serialization.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import logging
from pathlib import Path
from typing import Any, Callable

from curl_cffi.requests import AsyncSession

from spygram.cache import Cache
from spygram.models import HighlightGroup, MediaItem

logger = logging.getLogger("spygram.downloader")


class DownloadStatus(str, Enum):
    """
    Status outcomes for individual resource downloads.
    """
    DOWNLOADED = "downloaded"
    CACHED = "cached"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True, frozen=True)
class DownloadEvent:
    """
    Progress event emitted when a file or item download operation concludes.

    :param item_id: Unique Instagram media ID.
    :type item_id: str
    :param filename: Saved filename or prefix.
    :type filename: str
    :param status: DownloadStatus outcome.
    :type status: DownloadStatus
    :param size_bytes: Total size in bytes of the downloaded file.
    :type size_bytes: int
    :param error_message: Optional error description if failed.
    :type error_message: str | None
    """

    item_id: str
    filename: str
    status: DownloadStatus
    size_bytes: int = 0
    error_message: str | None = None


class Downloader:
    """
    Asynchronous binary file downloader and media organizer.

    :param cache: Persistent cache repository for tracking downloaded media.
    :type cache: Cache | None
    :param max_concurrent: Maximum number of parallel file download streams.
    :type max_concurrent: int
    :param proxy: Optional HTTP or SOCKS5 proxy URL.
    :type proxy: str | None
    :param timeout: Network streaming timeout in seconds.
    :type timeout: float
    """

    def __init__(
        self,
        cache: Cache | None = None,
        max_concurrent: int = 3,
        proxy: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.cache = cache
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.timeout = timeout
        self._session: AsyncSession | None = None
        self._stats_lock = asyncio.Lock()

    async def __aenter__(self) -> Downloader:
        self._ensure_session()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def session(self) -> AsyncSession:
        """
        Active AsyncSession for downloading binary files.
        """
        return self._ensure_session()

    def _ensure_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(
                impersonate="chrome131",
                timeout=self.timeout,
                proxies=self.proxies,
            )
        return self._session

    async def close(self) -> None:
        """
        Close the underlying HTTP download session and release resources.
        """
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def download_file(
        self,
        url: str,
        dest_path: Path,
        media_id: str | None = None,
        force: bool = False,
        search_patterns: tuple[str, ...] = (),
    ) -> tuple[DownloadStatus, int]:
        """
        Download a file atomically with temporary file swapping and multi-tier cache deduplication.

        :param url: Direct binary resource URL.
        :type url: str
        :param dest_path: Final target destination path on disk.
        :type dest_path: Path
        :param media_id: Unique cache identifier. Defaults to resolved file path.
        :type media_id: str | None
        :param force: If True, bypass cache and redownload unconditionally.
        :type force: bool
        :param search_patterns: Optional glob patterns to search for existing files with matching identifiers.
        :type search_patterns: tuple[str, ...]
        :return: Tuple of (DownloadStatus, bytes_downloaded).
        :rtype: tuple[DownloadStatus, int]
        """
        status, size, _ = await self._download_file_internal(
            url=url,
            dest_path=dest_path,
            media_id=media_id,
            force=force,
            search_patterns=search_patterns,
        )
        return status, size

    async def _download_file_internal(
        self,
        url: str,
        dest_path: Path,
        media_id: str | None = None,
        force: bool = False,
        search_patterns: tuple[str, ...] = (),
    ) -> tuple[DownloadStatus, int, str | None]:
        """
        Internal implementation of file download with detailed error diagnostic return.
        """
        cache_id = media_id or str(dest_path.resolve().as_posix())

        if not force and self.cache:
            if await self.cache.is_media_downloaded(cache_id, str(dest_path)):
                file_size = dest_path.stat().st_size if dest_path.is_file() else 0
                logger.debug("Cache hit for media '%s' (%s)", cache_id, dest_path.name)
                return DownloadStatus.CACHED, file_size, None

        if not force and dest_path.is_file() and dest_path.stat().st_size > 0:
            file_size = dest_path.stat().st_size
            if self.cache:
                await self.cache.register_media(cache_id, str(dest_path), file_size)
            logger.debug("Found existing file on disk for '%s' (%d bytes)", dest_path.name, file_size)
            return DownloadStatus.CACHED, file_size, None

        if not force and search_patterns and dest_path.parent.is_dir():
            for pattern in search_patterns:
                for candidate in dest_path.parent.glob(pattern):
                    if candidate.is_file():
                        try:
                            candidate_size = candidate.stat().st_size
                            if candidate_size > 0:
                                if candidate.resolve() != dest_path.resolve():
                                    candidate.replace(dest_path)
                                    file_size = dest_path.stat().st_size
                                else:
                                    file_size = candidate_size

                                if self.cache:
                                    await self.cache.register_media(cache_id, str(dest_path), file_size)
                                logger.debug("Matched existing candidate pattern '%s' for '%s'", pattern, dest_path.name)
                                return DownloadStatus.CACHED, file_size, None
                        except OSError:
                            continue

        session = self._ensure_session()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")

        try:
            logger.debug("Downloading stream: %s -> %s", dest_path.name, temp_path.name)
            async with session.stream("GET", url, allow_redirects=True) as response:
                if response.status_code != 200:
                    temp_path.unlink(missing_ok=True)
                    err = f"HTTP {response.status_code}"
                    logger.debug("Download stream rejected with status %d for %s", response.status_code, dest_path.name)
                    return DownloadStatus.FAILED, 0, err

                with open(temp_path, "wb") as file_handle:
                    async for chunk in response.aiter_content():
                        file_handle.write(chunk)

            if temp_path.is_file() and temp_path.stat().st_size > 0:
                temp_path.replace(dest_path)
                file_size = dest_path.stat().st_size
                if self.cache:
                    await self.cache.register_media(cache_id, str(dest_path), file_size)
                logger.debug("Successfully saved %s (%d bytes)", dest_path.name, file_size)
                return DownloadStatus.DOWNLOADED, file_size, None

            temp_path.unlink(missing_ok=True)
            return DownloadStatus.FAILED, 0, "Empty payload received"

        except OSError as e:
            temp_path.unlink(missing_ok=True)
            if e.errno in (28, 122):  # ENOSPC / EDQUOT (Disk full)
                err_msg = f"Disk full: {e}"
                logger.error("Storage full while saving %s: %s", dest_path.name, e)
            else:
                err_msg = f"Filesystem I/O error: {e}"
                logger.debug("I/O error during download of %s: %s", dest_path.name, e)
            return DownloadStatus.FAILED, 0, err_msg
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            logger.debug("Network/stream error downloading %s: %s", dest_path.name, e)
            return DownloadStatus.FAILED, 0, str(e)

    async def download_profile_pic(self, url: str, user_dir: Path) -> tuple[DownloadStatus, int]:
        """
        Download the target user's high-definition profile picture.

        :param url: High-resolution profile picture URL.
        :type url: str
        :param user_dir: Root directory for the target user.
        :type user_dir: Path
        :return: Tuple of (DownloadStatus, file_size).
        :rtype: tuple[DownloadStatus, int]
        """
        if not url:
            return DownloadStatus.SKIPPED, 0

        target_file = user_dir / "profile_pic.jpg"
        # Cache-aware: only redownload when the file is missing or changed,
        # instead of forcing a fresh copy on every single run.
        return await self.download_file(url, target_file, force=False)

    async def download_item(
        self,
        item: MediaItem,
        dest_dir: Path,
    ) -> list[DownloadEvent]:
        """
        Download all binary resources associated with a single :class:`MediaItem`.

        :param item: Domain entity to download.
        :type item: MediaItem
        :param dest_dir: Destination folder where media files will be placed.
        :type dest_dir: Path
        :return: List of download events for each resource in the item.
        :rtype: list[DownloadEvent]
        """
        events: list[DownloadEvent] = []
        prefix = item.filename_prefix

        if not item.resources:
            return events

        for resource in item.resources:
            filename = f"{prefix}{resource.suffix}.{resource.extension}"
            target_path = dest_dir / filename
            resource_id = f"{item.id}{resource.suffix}"

            search_patterns = (
                f"*_{item.code}{resource.suffix}.{resource.extension}",
                f"*_{item.id}{resource.suffix}.{resource.extension}",
            )

            status, size, err_msg = await self._download_file_internal(
                url=resource.url,
                dest_path=target_path,
                media_id=resource_id,
                search_patterns=search_patterns,
            )
            events.append(
                DownloadEvent(
                    item_id=item.id,
                    filename=filename,
                    status=status,
                    size_bytes=size,
                    error_message=err_msg,
                )
            )

        return events

    async def download_batch(
        self,
        items: list[MediaItem] | list[tuple[MediaItem, Path]],
        dest_dir: Path | None = None,
        on_progress: Callable[[DownloadEvent], None] | None = None,
    ) -> dict[str, int]:
        """
        Process and download a collection of media items concurrently.

        :param items: List of items or (item, destination_path) tuples.
        :type items: list[MediaItem] | list[tuple[MediaItem, Path]]
        :param dest_dir: Default destination folder if items are not paired with paths.
        :type dest_dir: Path | None
        :param on_progress: Optional callback invoked upon completing each item.
        :type on_progress: Callable[[DownloadEvent], None] | None
        :return: Summary dictionary with item counts ('downloaded', 'cached', 'failed', 'total').
        :rtype: dict[str, int]
        """
        stats = {"downloaded": 0, "cached": 0, "failed": 0, "total": len(items)}

        normalized_items: list[tuple[MediaItem, Path]] = []
        for entry in items:
            if isinstance(entry, tuple):
                normalized_items.append(entry)
            elif dest_dir is not None:
                normalized_items.append((entry, dest_dir))
            else:
                raise ValueError("dest_dir must be provided when passing un-paired MediaItems")

        async def _worker(item: MediaItem, target_folder: Path) -> None:
            async with self.semaphore:
                try:
                    events = await self.download_item(item, target_folder)

                    failed_events = [e for e in events if e.status == DownloadStatus.FAILED]
                    async with self._stats_lock:
                        if failed_events:
                            item_status = DownloadStatus.FAILED
                            stats["failed"] += 1
                            first_err = failed_events[0].error_message
                        elif any(e.status == DownloadStatus.DOWNLOADED for e in events):
                            item_status = DownloadStatus.DOWNLOADED
                            stats["downloaded"] += 1
                            first_err = None
                        else:
                            item_status = DownloadStatus.CACHED
                            stats["cached"] += 1
                            first_err = None

                    if on_progress:
                        on_progress(
                            DownloadEvent(
                                item_id=item.id,
                                filename=item.filename_prefix,
                                status=item_status,
                                size_bytes=sum(e.size_bytes for e in events),
                                error_message=first_err,
                            )
                        )
                except Exception as e:
                    async with self._stats_lock:
                        stats["failed"] += 1
                    logger.debug("Worker exception while downloading item %s: %s", item.id, e)
                    if on_progress:
                        on_progress(
                            DownloadEvent(
                                item_id=item.id,
                                filename=item.filename_prefix,
                                status=DownloadStatus.FAILED,
                                size_bytes=0,
                                error_message=str(e),
                            )
                        )

        await asyncio.gather(*(_worker(item, folder) for item, folder in normalized_items))
        return stats

    async def download_highlight_group(
        self,
        group: HighlightGroup,
        base_dir: Path,
        on_progress: Callable[[DownloadEvent], None] | None = None,
    ) -> dict[str, int]:
        """
        Download all media items contained within a highlight group.

        :param group: Highlight group entity.
        :type group: HighlightGroup
        :param base_dir: Base highlights folder.
        :type base_dir: Path
        :param on_progress: Optional progress callback.
        :type on_progress: Callable[[DownloadEvent], None] | None
        :return: Summary dictionary of download outcomes.
        :rtype: dict[str, int]
        """
        target_dir = base_dir / group.slug
        return await self.download_batch(
            items=group.items,
            dest_dir=target_dir,
            on_progress=on_progress,
        )

    @staticmethod
    def save_index_metadata(data: dict[str, Any] | list[dict[str, Any]], target_file: Path) -> Path:
        """
        Save structured index metadata in pretty-printed UTF-8 JSON format.

        :param data: JSON-serializable dictionary or list.
        :type data: dict[str, Any] | list[dict[str, Any]]
        :param target_file: Path to destination JSON file.
        :type target_file: Path
        :return: The written file path.
        :rtype: Path
        """
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return target_file