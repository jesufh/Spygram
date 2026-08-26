"""
spygram.cli
~~~~~~~~~~~

Command Line Interface (CLI) application and terminal user interface.

Coordinates user input parsing, browser/saved session authentication,
retro ASCII progress reporting, diagnostic logging, and scraping task orchestration.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import sys
import time
from typing import Any

from rich.console import Console
from rich.progress import Progress, ProgressColumn, TextColumn
from rich.prompt import Prompt
from rich.text import Text

from spygram import __version__
from spygram.auth import (
    extract_browser_cookies,
    list_saved_sessions,
    load_session,
    save_session,
)
from spygram.cache import Cache
from spygram.client import InstagramClient
from spygram.config import AppConfig, get_default_downloads_dir, validate_username
from spygram.downloader import DownloadEvent, Downloader
from spygram.exceptions import (
    ActionBlockedError,
    CheckpointError,
    ConnectionTimeoutError,
    GraphQLQueryError,
    LoginRequiredError,
    NetworkConnectionError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    SpygramError,
)
from spygram.logger import setup_logging
from spygram.models import MediaItem

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

console = Console(force_terminal=True)
"""Rich console instance for colored CLI output."""

logger = logging.getLogger("spygram.cli")
"""Module logger for CLI operations."""

BANNER = rf"""[grey70]
 ___ ___ _ _ ___ ___ ___ _____ 
|_ -| . | | | . |  _| .'|     |
|___|  _|_  |_  |_| |__,|_|_|_|
    |_| |___|___| version {__version__}  
[/grey70]"""
"""ASCII art logo banner displayed at application startup."""


class RetroBarColumn(ProgressColumn):
    """
    Retro-styled ASCII progress bar column.
    """

    def render(self, task: Any) -> Text:
        if task.total is None:
            return Text("fetching...", style="grey50")
        ratio = (task.completed / task.total) if task.total > 0 else 0.0
        ratio = min(max(ratio, 0.0), 1.0)
        pct = ratio * 100.0
        filled = int(ratio * 20)
        return Text(f"[{'#' * filled + '.' * (20 - filled)}] {pct:3.0f}%", style="grey70")


class RetroStatusColumn(ProgressColumn):
    """
    Status description column for download counts.
    """

    def render(self, task: Any) -> Text:
        info = task.fields.get("info", "")
        return Text(f" ({info})" if info else "", style="grey50")


def create_retro_progress() -> Progress:
    """
    Create a preconfigured Rich Progress instance with retro styling.

    :return: Progress bar renderer.
    :rtype: Progress
    """
    return Progress(
        TextColumn("[white]{task.description:<17}"),
        RetroBarColumn(),
        RetroStatusColumn(),
        console=console,
    )


def format_size(size_bytes: int | float) -> str:
    """
    Convert raw bytes into human-readable metric units.

    :param size_bytes: Total number of bytes.
    :type size_bytes: int | float
    :return: Formatted string (e.g., '14.2 mb').
    :rtype: str
    """
    size = float(size_bytes)
    for unit in ("b", "kb", "mb", "gb"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} tb"


def calculate_dir_size(directory: Path) -> int:
    """
    Calculate the total size in bytes of all files in a directory.

    :param directory: Root directory to measure.
    :type directory: Path
    :return: Total size in bytes.
    :rtype: int
    """
    if not directory.is_dir():
        return 0
    total = 0
    for file_path in directory.rglob("*"):
        try:
            if file_path.is_file():
                total += file_path.stat().st_size
        except OSError:
            continue
    return total


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    :return: Parsed arguments namespace.
    :rtype: argparse.Namespace
    """
    parser = argparse.ArgumentParser(prog="spygram", description="spygram - instagram scraper")
    parser.add_argument("--user", "-u", required=True, help="target username to scrape")

    auth_group = parser.add_mutually_exclusive_group(required=False)
    auth_group.add_argument(
        "--browser-cookies",
        "-b",
        choices=["chrome", "edge", "firefox", "brave", "opera", "chromium", "vivaldi", "safari"],
        metavar="BROWSER",
        help="extract cookies from installed browser",
    )
    auth_group.add_argument(
        "--session",
        "-s",
        type=str,
        nargs="?",
        const="",
        metavar="NAME",
        help="load saved session (omit NAME to choose interactively)",
    )

    parser.add_argument("--all", "-a", action="store_true", help="scrape all available content types")
    parser.add_argument("--posts", action="store_true", help="scrape regular posts")
    parser.add_argument("--stories", action="store_true", help="scrape active stories")
    parser.add_argument("--reels", action="store_true", help="scrape reels")
    parser.add_argument("--highlights", action="store_true", help="scrape highlights")
    parser.add_argument("--tagged", action="store_true", help="scrape tagged posts")
    parser.add_argument("--limit", "-l", type=int, default=0, help="limit number of items to download (0 = unlimited)")
    parser.add_argument("--since", type=str, default=None, help="download only media posted after ISO date (YYYY-MM-DD)")
    parser.add_argument("--proxy", "-p", type=str, default=None, help="proxy URL (e.g. http://127.0.0.1:8080)")
    parser.add_argument("--output-dir", "-o", type=Path, default=None, help="custom base download directory")
    parser.add_argument("--max-concurrent", "-c", type=int, default=3, help="maximum concurrent file downloads")
    parser.add_argument("--version", "-v", action="version", version=f"spygram {__version__}")
    parser.add_argument("--clear-cache", action="store_true", help="clear local cache database")
    parser.add_argument("--verbose", action="store_true", help="enable informational logging output")
    parser.add_argument("--debug", "-d", action="store_true", help="enable detailed debug traces and exception stack traces")
    parser.add_argument("--log-file", type=Path, default=None, help="write structured debug logs to specified file")

    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be >= 0 (0 means unlimited)")
    if args.max_concurrent < 1:
        parser.error("--max-concurrent must be >= 1")
    return args


def select_session_interactive(config: AppConfig) -> tuple[str, dict[str, str]] | None:
    """
    Prompt user to select from available saved session files.

    :param config: Application configuration.
    :type config: AppConfig
    :return: Tuple of (username, cookies) or None.
    :rtype: tuple[str, dict[str, str]] | None
    """
    sessions = list_saved_sessions(config.sessions_dir)
    if not sessions:
        console.print("no saved sessions found", style="grey70")
        return None

    console.print("available sessions:")
    for i, uname in enumerate(sessions, 1):
        console.print(f"  {i}. {uname}", style="grey70")

    choice = Prompt.ask("\nselect a session", choices=[str(i) for i in range(1, len(sessions) + 1)])
    selected_user = sessions[int(choice) - 1]
    cookies = load_session(selected_user, config.sessions_dir)
    if cookies:
        return selected_user, cookies

    console.print(f"error: failed to load session for {selected_user}", style="grey70")
    return None


async def run_scraper_category(
    client: InstagramClient,
    downloader: Downloader,
    target: str,
    user_id: str,
    ctype: str,
    limit: int,
    config: AppConfig,
    since: datetime | None = None,
) -> None:
    """
    Execute extraction and concurrent downloads for a single content category.

    :param client: Configured InstagramClient.
    :type client: InstagramClient
    :param downloader: Configured Downloader service.
    :type downloader: Downloader
    :param target: Target username.
    :type target: str
    :param user_id: Target numeric user ID.
    :type user_id: str
    :param ctype: Category name ('posts', 'stories', 'reels', 'highlights', 'tagged').
    :type ctype: str
    :param limit: Maximum items limit.
    :type limit: int
    :param config: Application configuration.
    :type config: AppConfig
    :param since: Optional UTC datetime cutoff for incremental scraping.
    :type since: datetime | None
    """
    progress = create_retro_progress()
    cdir = config.get_content_downloads_dir(target, ctype)

    with progress:
        task_id = progress.add_task(ctype, total=None)

        items: list[MediaItem] = []
        item_pairs: list[tuple[MediaItem, Path]] = []

        if ctype == "posts":
            async for item in client.iter_posts(user_id, limit=limit, since=since):
                items.append(item)
                item_pairs.append((item, cdir))
        elif ctype == "reels":
            async for item in client.iter_reels(user_id, username=target, limit=limit, since=since):
                items.append(item)
                item_pairs.append((item, cdir))
        elif ctype == "stories":
            async for item in client.iter_stories(user_id):
                items.append(item)
                item_pairs.append((item, cdir))
        elif ctype == "tagged":
            async for item in client.iter_tagged(user_id, limit=limit, since=since):
                items.append(item)
                item_pairs.append((item, cdir))
        elif ctype == "highlights":
            async for group in client.iter_highlights(user_id):
                hdir = cdir / group.slug
                for it in group.items:
                    items.append(it)
                    item_pairs.append((it, hdir))

        if not items:
            progress.update(task_id, total=100, completed=0, info=f"no {ctype} found")
            return

        # Respect the user's --limit even when a single page yields more
        # items than requested (the iterator only stops *fetching*, it may
        # still hand back a full page).
        if limit > 0:
            items = items[:limit]
            item_pairs = item_pairs[:limit]

        progress.update(task_id, total=len(item_pairs), completed=0)

        def _on_event(event: DownloadEvent) -> None:
            progress.advance(task_id)

        stats = await downloader.download_batch(
            items=item_pairs,
            on_progress=_on_event,
        )

        progress.update(task_id, info=f"{stats['downloaded']} downloaded, {stats['cached']} cached")

    meta_list = [it.to_metadata_dict() for it in items]
    downloader.save_index_metadata(
        {"username": target, "total": len(items), ctype: meta_list},
        cdir / f"_{ctype}_index.json",
    )


async def run() -> None:
    """
    Main asynchronous CLI application runner.
    """
    args = parse_args()
    if args.debug:
        log_level = logging.DEBUG
    elif args.verbose:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING

    setup_logging(level=log_level, log_file=args.log_file, use_rich=True)

    downloads_base = args.output_dir or get_default_downloads_dir()
    config = AppConfig(
        downloads_dir=downloads_base,
        max_concurrent_downloads=args.max_concurrent,
    )
    console.print(BANNER)

    since_dt: datetime | None = None
    if args.since:
        try:
            since_dt = datetime.fromisoformat(args.since)

            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            else:
                since_dt = since_dt.astimezone(timezone.utc)
        except ValueError:
            console.print(f"error: invalid date format '{args.since}', expected YYYY-MM-DD", style="grey70")
            return

        if since_dt > datetime.now(timezone.utc):
            console.print(f"warning: --since '{args.since}' is in the future; no media will be downloaded.", style="yellow")

    cache = Cache(config.cache_db_path)
    if args.clear_cache:
        console.print("clearing local cache database...", style="grey50")
        await cache.clear_all()
        console.print("cache cleared successfully.\n", style="grey50")

    cookies: dict[str, str] | None = None
    try:
        target = validate_username(args.user)
    except ValueError as e:
        console.print(f"error: {e}", style="grey70")
        cache.close()
        return

    if args.session is not None:
        if args.session == "":
            res = select_session_interactive(config)
            if res:
                _, cookies = res
            else:
                cache.close()
                return
        else:
            cookies = load_session(args.session, config.sessions_dir)
            if not cookies:
                console.print(f"error: session '{args.session}' not found.", style="grey70")
                cache.close()
                return
    elif args.browser_cookies:
        cookies = extract_browser_cookies(args.browser_cookies)

    client = InstagramClient(
        session_cookies=cookies,
        proxy=args.proxy,
        cache=cache,
        max_retries=config.max_retries,
        timeout=config.request_timeout,
    )
    downloader = Downloader(
        cache=cache,
        max_concurrent=config.max_concurrent_downloads,
        proxy=args.proxy,
        timeout=config.download_timeout,
    )

    try:
        if (args.session or args.browser_cookies) and not client.is_authenticated:
            console.print("warning: authentication failed, falling back to anonymous mode.", style="yellow")
        elif not cookies:
            console.print("info: running in anonymous mode (some content types may be restricted).", style="grey50")

        if args.browser_cookies and client.user_id:
            try:
                uname = await client.get_username_by_user_id(client.user_id)
                save_session(cookies, uname, config.sessions_dir)
                console.print(f"session saved for {uname}", style="grey50")
            except Exception as e:
                logger.debug("Failed to persist browser cookies: %s", e)

        types = ["posts", "stories", "reels", "highlights"] if args.all else [
            f for f in ("posts", "stories", "reels", "highlights", "tagged") if getattr(args, f, False)
        ]
        if not types:
            types = ["posts"]

        console.print(f"target account   {target}", style="white")
        console.print(f"content types    {', '.join(types)}\n", style="grey70")

        try:
            profile = await client.get_profile(target)
            user_id = profile.id or await client.get_user_id(target)

            if profile.is_private and str(profile.id) != str(client.user_id):
                if not client.is_authenticated:
                    console.print(f"error: target account '{target}' is private. authentication required.", style="grey70")
                    return

                try:
                    friendship = await client.get_friendship_status(user_id)
                    if not friendship.get("following", False):
                        console.print(f"error: target account '{target}' is private. you must be following this account to scrape it.", style="grey70")
                        return
                except Exception as e:
                    logger.debug("friendship status check failed for user '%s': %s", target, e)
                    console.print(f"error: could not verify access to private account '{target}'. make sure you are following it.", style="grey70")
                    return
        except NotFoundError:
            console.print(f"error: target user '{target}' not found.", style="grey70")
            return
        except CheckpointError as e:
            console.print(f"error: instagram checkpoint triggered ({e})", style="grey70")
            return
        except ActionBlockedError as e:
            console.print(f"error: instagram action temporarily blocked ({e})", style="grey70")
            return
        except RateLimitError as e:
            console.print(f"error: rate limit hit ({e})", style="grey70")
            return
        except (NetworkConnectionError, ConnectionTimeoutError) as e:
            console.print(f"error: network connection failed ({e})", style="grey70")
            return
        except LoginRequiredError as e:
            console.print(f"error: authentication required ({e})", style="grey70")
            return
        except Exception as e:
            console.print(f"error: lookup failed ({e})", style="grey70")
            if args.debug:
                console.print_exception(show_locals=False)
            return

        try:
            hd_avatar_url = await client.get_user_hd_profile_pic(user_id=user_id, username=target)
            if hd_avatar_url:
                await downloader.download_profile_pic(hd_avatar_url, config.get_user_downloads_dir(target))
        except Exception as e:
            logger.debug("Failed to download profile picture: %s", e)

        start_time = time.time()
        for ctype in types:
            try:
                await run_scraper_category(
                    client=client,
                    downloader=downloader,
                    target=target,
                    user_id=user_id,
                    ctype=ctype,
                    limit=args.limit,
                    config=config,
                    since=since_dt,
                )
            except (LoginRequiredError, CheckpointError, ActionBlockedError, RateLimitError) as e:
                console.print(f"{ctype:<17}error: {e}", style="grey70")
                break
            except Exception as e:
                console.print(f"{ctype:<17}error: {e}", style="grey70")
                if args.debug:
                    console.print_exception(show_locals=False)

        elapsed = time.time() - start_time
        total_size = calculate_dir_size(config.get_user_downloads_dir(target))
        console.print(
            f"\nfinished. {format_size(total_size)} saved in downloads/{target}/ in {elapsed:.1f} seconds.",
            style="white",
        )

    finally:
        await client.close()
        await downloader.close()
        cache.close()


def main() -> None:
    """
    CLI script entrypoint.
    """
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\noperation cancelled by user.", style="grey50")