"""Highlights scraper for Spygram."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from spygram.client import SpygramWebClient
from spygram.config import get_content_dir, random_delay
from spygram.utils import save_metadata, slugify

console = Console(force_terminal=True)


async def scrape_highlights(
    client: SpygramWebClient,
    username: str,
    user_id: str,
) -> dict:
    """Scrape all highlights. Each highlight gets a named subfolder."""
    console.print(f"\n  [bold cyan]Fetching highlights for @{username}...[/]")
    content_dir = get_content_dir(username, "highlights")

    highlights = await client.get_highlights_tray(user_id)

    if not highlights:
        console.print("  [yellow]No highlights found[/]")
        return {"total_highlights": 0, "total_items": 0, "downloaded": 0, "errors": 0}

    console.print(f"  [dim]Found {len(highlights)} highlights[/]")

    total_items = 0
    downloaded = 0
    errors = 0
    all_metadata = []

    for hl in highlights:
        try:
            hl_id_raw = str(hl.get("id", ""))
            hl_id = hl_id_raw.replace("highlight:", "")
            hl_title = hl.get("title") or "Untitled"
            hl_slug = slugify(hl_title) or f"highlight_{hl_id}"
            hl_dir = content_dir / hl_slug
            hl_dir.mkdir(parents=True, exist_ok=True)

            console.print(f"  [dim]-> {hl_title}[/]")

            data = await client.get_highlight_stories(hl_id)
            reels = data.get("reels", {})
            highlight_reel = reels.get(f"highlight:{hl_id}", {})
            items = highlight_reel.get("items", [])
            total_items += len(items)

            hl_meta = {
                "id": hl_id,
                "title": hl_title,
                "items_count": len(items),
            }

            if items:
                with Progress(
                    SpinnerColumn(), TextColumn(f"[dim]{hl_title}[/]"),
                    BarColumn(bar_width=20), MofNCompleteColumn(),
                    console=console, transient=True,
                ) as progress:
                    task = progress.add_task("Downloading items", total=len(items))

                    for item in items:
                        try:
                            pk = item.get("pk")
                            media_type = item.get("media_type")
                            is_video = media_type == 2

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
                                await client.download_file(url, hl_dir / f"{pk}.{ext}")
                                downloaded += 1

                        except Exception as e:
                            console.print(f"    [red]Error:[/] {e}")
                            errors += 1

                        progress.advance(task)
                        await asyncio.sleep(random_delay() * 0.3)

            all_metadata.append(hl_meta)

        except Exception as e:
            console.print(f"  [red]Error in highlight:[/] {e}")
            errors += 1

    save_metadata(
        {
            "username": username,
            "total_highlights": len(highlights),
            "total_items": total_items,
            "highlights": all_metadata,
        },
        content_dir / "_highlights_index.json",
    )

    console.print(
        f"  [green]{downloaded} items from {len(highlights)} highlights downloaded[/]"
        + (f" [yellow]({errors} errors)[/]" if errors else "")
    )
    return {"total_highlights": len(highlights), "total_items": total_items, "downloaded": downloaded, "errors": errors}
