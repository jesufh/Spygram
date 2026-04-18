"""Async entry point for Spygram."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import httpx
from rich.console import Console
from rich.table import Table
from rich import box

from spygram import __version__
from spygram.client import SpygramWebClient
from spygram.config import get_user_dir
from spygram.utils import format_size, calculate_dir_size

console = Console(force_terminal=True)

BANNER = r"""[bold cyan]
   ____                                       
  / ___| _ __  _   _  __ _ _ __ __ _ _ __ ___  
  \___ \| '_ \| | | |/ _` | '__/ _` | '_ ` _ \ 
   ___) | |_) | |_| | (_| | | | (_| | | | | | |
  |____/| .__/ \__, |\__, |_|  \__,_|_| |_| |_|
        |_|    |___/ |___/                      
[/bold cyan]"""


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="spygram",
        description="Spygram - Instagram scraper",
    )

    parser.add_argument("--user", "-u", required=True, help="Target username")

    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--browser-cookies", "-b", action="store_true", help="Authenticate via browser cookies (default)")
    auth.add_argument("--session", "-s", type=str, metavar="NAME", help="Load saved session by name")
    auth.add_argument("--session-id", type=str, metavar="ID", help="Authenticate via raw session ID cookie")

    parser.add_argument("--all", "-a", action="store_true", help="Scrape everything")
    parser.add_argument("--profile", action="store_true", help="Profile info and photo")
    parser.add_argument("--posts", action="store_true", help="Feed posts")
    parser.add_argument("--stories", action="store_true", help="Active stories")
    parser.add_argument("--reels", action="store_true", help="Reels/clips")
    parser.add_argument("--highlights", action="store_true", help="Highlights")
    parser.add_argument("--tagged", action="store_true", help="Tagged posts")
    parser.add_argument("--saved", action="store_true", help="Saved posts (own account)")

    parser.add_argument("--limit", "-l", type=int, default=0, help="Maximum items (0 = all)")
    parser.add_argument("--version", "-v", action="version", version=f"spygram {__version__}")

    return parser.parse_args()


async def run() -> None:
    """Async main runner."""
    args = parse_args()

    console.print(BANNER)
    console.print(f"  [dim]v{__version__}[/]\n")

    from spygram.auth import (
        login_with_browser_cookies,
        login_with_saved_session,
        login_with_session_id,
        save_session,
        load_session,
        get_latest_session,
    )

    client = SpygramWebClient()
    cj = None
    logged_username = None
    target = args.user.lstrip("@")

    if args.session:
        result = login_with_saved_session(args.session)
        if result:
            logged_username, cj = result
    elif args.session_id:
        cj = login_with_session_id(args.session_id)
    elif args.browser_cookies:
        cj = login_with_browser_cookies()
    else:
        if (target_cj := load_session(target)):
            console.print(f"  [dim]Auto-loading session for @{target}[/]")
            cj = target_cj
            logged_username = target
        elif (latest_user := get_latest_session()):
            console.print(f"  [dim]Auto-loading latest session (@{latest_user})[/]")
            cj = load_session(latest_user)
            logged_username = latest_user
        else:
            cj = login_with_browser_cookies()

    if cj:
        client.load_cookies(cj)

    if not client.is_authenticated:
        console.print("  [red]Error: authentication failed. Check your cookies/session.[/]")
        sys.exit(1)

    target = args.user.lstrip("@")
    content_types: list[str] = []

    if args.all:
        content_types = ["profile", "posts", "stories", "reels", "highlights"]
    else:
        for flag in ("profile", "posts", "stories", "reels", "highlights", "tagged", "saved"):
            if getattr(args, flag, False):
                content_types.append(flag)

    if not content_types:
        content_types = ["profile"]

    console.print(f"  [bold green]Scraping @{target}[/]")
    console.print(f"  [dim]Content: {', '.join(content_types)}[/]\n")

    try:
        user_id = await client.get_user_id(target)
        console.print(f"  [dim]User ID: {user_id}[/]")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            console.print("  [red]Error: session expired (401). Please authenticate again.[/]")
        else:
            console.print(f"  [red]Error: could not find @{target}: {e}[/]")
        await client.close()
        sys.exit(1)
    except Exception as e:
        console.print(f"  [red]Error looking up @{target}: {e}[/]")
        await client.close()
        sys.exit(1)

    from spygram.scrapers.profile import scrape_profile
    from spygram.scrapers.posts import scrape_posts
    from spygram.scrapers.stories import scrape_stories
    from spygram.scrapers.reels import scrape_reels
    from spygram.scrapers.highlights import scrape_highlights
    from spygram.scrapers.tagged import scrape_tagged
    from spygram.scrapers.saved import scrape_saved

    start = time.time()
    results: dict[str, dict] = {}

    for ctype in content_types:
        try:
            if ctype == "profile":
                results["profile"] = await scrape_profile(client, target, user_id)
            elif ctype == "posts":
                results["posts"] = await scrape_posts(client, target, user_id, limit=args.limit)
            elif ctype == "stories":
                results["stories"] = await scrape_stories(client, target, user_id)
            elif ctype == "reels":
                results["reels"] = await scrape_reels(client, target, user_id, limit=args.limit)
            elif ctype == "highlights":
                results["highlights"] = await scrape_highlights(client, target, user_id)
            elif ctype == "tagged":
                results["tagged"] = await scrape_tagged(client, target, user_id, limit=args.limit)
            elif ctype == "saved":
                results["saved"] = await scrape_saved(client, target, limit=args.limit or 50)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                console.print("  [red]Error: session expired (401).[/]")
                break
            console.print(f"  [red]Error in {ctype}: {e}[/]")
        except Exception as e:
            console.print(f"  [red]Error in {ctype}: {e}[/]")

    elapsed = time.time() - start
    await client.close()

    user_dir = get_user_dir(target)
    total_size = calculate_dir_size(user_dir)

    table = Table(title="Summary", box=box.ROUNDED, border_style="green", show_lines=True)
    table.add_column("Type", style="cyan", min_width=12)
    table.add_column("Total", justify="right")
    table.add_column("Downloaded", justify="right", style="green")
    table.add_column("Errors", justify="right", style="red")

    for ctype, data in results.items():
        if isinstance(data, dict):
            total = str(data.get("total", data.get("total_items", "-")))
            dl = str(data.get("downloaded", "-"))
            err = str(data.get("errors", 0))
            table.add_row(ctype.capitalize(), total, dl, err)

    console.print()
    console.print(table)
    console.print(
        f"\n  [bold green]Completed in {elapsed:.1f}s[/]"
        f"  |  [dim]{format_size(total_size)}[/]"
        f"  |  [dim]downloads/{target}/[/]\n"
    )

    if cj and logged_username is None:
        username_from_cookie = None
        for cookie in client.client.cookies.jar:
            if cookie.name == "ds_user_id":
                username_from_cookie = target
        if username_from_cookie:
            save_session(cj, username_from_cookie)


def main() -> None:
    """Synchronous wrapper for the async runner."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
