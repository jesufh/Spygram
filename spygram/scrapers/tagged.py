"""Tagged posts scraper for Spygram."""

from __future__ import annotations

from rich.console import Console
from spygram.client import SpygramWebClient

console = Console(force_terminal=True)


async def scrape_tagged(
    client: SpygramWebClient,
    username: str,
    user_id: str,
) -> dict:
    """Scrape tagged posts. Currently not implemented."""
    console.print(f"\n  [bold cyan]Tagged posts for @{username}[/]")
    console.print("  [yellow]Not available yet (no stable endpoint)[/]")
    return {"total": 0, "downloaded": 0, "errors": 0}
