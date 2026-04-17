"""Reels scraper for Spygram."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from spygram.client import SpygramWebClient
from spygram.config import get_content_dir, random_delay, DEFAULT_REELS_LIMIT
from spygram.utils import save_metadata

console = Console(force_terminal=True)


async def scrape_reels(
    client: SpygramWebClient,
    username: str,
    user_id: str,
    limit: int = DEFAULT_REELS_LIMIT,
) -> dict:
    """Scrape reels with a flat output and single index."""
    console.print(f"\n  [bold cyan]Fetching reels for @{username}...[/]")
    content_dir = get_content_dir(username, "reels")

    amount = limit if limit > 0 else 50
    try:
        clips = await client.get_user_clips(user_id, amount=amount)
    except Exception as e:
        console.print(f"  [yellow]Could not fetch reels:[/] {e}")
        return {"total": 0, "downloaded": 0, "errors": 0}

    if not clips:
        console.print("  [yellow]No reels found[/]")
        return {"total": 0, "downloaded": 0, "errors": 0}

    console.print(f"  [dim]Found {len(clips)} reels[/]")

    downloaded = 0
    errors = 0
    all_metadata = []

    with Progress(
        SpinnerColumn(), TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=25), MofNCompleteColumn(),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("Downloading reels", total=len(clips))

        for media in clips:
            try:
                code = media.get("code", "")
                ts = media.get("taken_at")
                taken_at = datetime.fromtimestamp(ts) if ts else datetime.now()

                caption_text = ""
                caption = media.get("caption")
                if caption:
                    caption_text = caption.get("text", "")

                meta = {
                    "id": str(media.get("pk")),
                    "code": code,
                    "taken_at": str(taken_at),
                    "caption": caption_text,
                    "like_count": media.get("like_count"),
                    "play_count": media.get("view_count") or media.get("play_count"),
                    "url": f"https://www.instagram.com/reel/{code}/",
                }
                all_metadata.append(meta)

                versions = media.get("video_versions", [])
                if versions:
                    await client.download_file(versions[0]["url"], content_dir / f"{code}.mp4")
                    downloaded += 1

            except Exception as e:
                console.print(f"  [red]Error in reel {media.get('code', '?')}:[/] {e}")
                errors += 1

            progress.advance(task)
            await asyncio.sleep(random_delay() * 0.3)

    save_metadata(
        {"username": username, "total": len(clips), "reels": all_metadata},
        content_dir / "_reels_index.json",
    )

    console.print(
        f"  [green]{downloaded} reels downloaded[/]"
        + (f" [yellow]({errors} errors)[/]" if errors else "")
    )
    return {"total": len(clips), "downloaded": downloaded, "errors": errors}
