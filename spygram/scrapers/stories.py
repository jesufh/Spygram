"""Stories scraper for Spygram."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from spygram.client import SpygramWebClient
from spygram.config import get_content_dir, random_delay
from spygram.utils import save_metadata, _make_serializable

console = Console(force_terminal=True)


async def scrape_stories(
    client: SpygramWebClient,
    username: str,
    user_id: str,
    user_only: bool = False,
) -> dict:
    """Scrape active stories with a flat output and single index."""
    console.print(f"\n  [bold cyan]Fetching stories for @{username}...[/]")
    content_dir = get_content_dir(username, "stories")

    items = await client.get_stories(user_id)

    if not items:
        console.print("  [yellow]No active stories found[/]")
        return {"total": 0, "downloaded": 0, "errors": 0}

    console.print(f"  [dim]Found {len(items)} stories[/]")

    downloaded = 0
    errors = 0
    all_metadata = []

    with Progress(
        SpinnerColumn(), TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=25), MofNCompleteColumn(),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("Downloading stories", total=len(items))

        for item in items:
            if user_only:
                if item.get("story_feed_media") or item.get("reshared_story"):
                    continue

            try:
                pk = item.get("pk")
                media_type = item.get("media_type")
                is_video = media_type == 2
                ts = item.get("taken_at")
                taken_at = datetime.fromtimestamp(ts) if ts else datetime.now()

                meta = {
                    "id": str(pk),
                    "type": "video" if is_video else "photo",
                    "taken_at": str(taken_at),
                    "expiring_at": str(item.get("expiring_at")),
                }
                all_metadata.append(meta)

                url = None
                ext = "jpg"

                if is_video:
                    versions = item.get("video_versions", [])
                    if versions:
                        url = versions[0].get("url")
                        ext = "mp4"
                else:
                    candidates = item.get("image_versions2", {}).get("candidates", [])
                    if candidates:
                        url = candidates[0].get("url")

                if url:
                    await client.download_file(url, content_dir / f"{pk}.{ext}")
                    downloaded += 1

            except Exception as e:
                console.print(f"  [red]Error in story {item.get('pk', '?')}:[/] {e}")
                errors += 1

            progress.advance(task)
            await asyncio.sleep(random_delay() * 0.3)

    save_metadata(
        {"username": username, "total": len(items), "stories": all_metadata},
        content_dir / "_stories_index.json",
    )

    console.print(
        f"  [green]{downloaded} stories downloaded[/]"
        + (f" [yellow]({errors} errors)[/]" if errors else "")
    )
    return {"total": len(items), "downloaded": downloaded, "errors": errors}
