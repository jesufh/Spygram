"""Saved posts scraper for Spygram."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from spygram.client import SpygramWebClient
from spygram.config import get_content_dir, random_delay
from spygram.utils import save_metadata

console = Console(force_terminal=True)


async def scrape_saved(
    client: SpygramWebClient,
    username: str,
    limit: int = 50,
) -> dict:
    """Scrape saved posts with a flat output and single index."""
    console.print(f"\n  [bold cyan]Fetching saved posts...[/]")
    content_dir = get_content_dir(username, "saved")

    try:
        medias = await client.get_saved_medias(amount=limit)
    except Exception as e:
        console.print(f"  [yellow]Could not fetch saved items:[/] {e}")
        return {"total": 0, "downloaded": 0, "errors": 0}

    if not medias:
        console.print("  [yellow]No saved posts found[/]")
        return {"total": 0, "downloaded": 0, "errors": 0}

    console.print(f"  [dim]Found {len(medias)} saved posts[/]")

    downloaded = 0
    errors = 0
    all_metadata = []

    with Progress(
        SpinnerColumn(), TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=25), MofNCompleteColumn(),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("Downloading saved items", total=len(medias))

        for media in medias:
            try:
                code = media.get("code", "")
                media_type = media.get("media_type")
                user = media.get("user", {})

                caption_text = ""
                cap = media.get("caption")
                if cap:
                    caption_text = cap.get("text", "")

                meta = {
                    "id": str(media.get("pk")),
                    "code": code,
                    "type": {1: "photo", 2: "video", 8: "album"}.get(media_type, "unknown"),
                    "caption": caption_text,
                    "owner": user.get("username", "unknown"),
                    "url": f"https://www.instagram.com/p/{code}/",
                }
                all_metadata.append(meta)

                if media_type == 1:
                    candidates = media.get("image_versions2", {}).get("candidates", [])
                    if candidates:
                        await client.download_file(candidates[0]["url"], content_dir / f"{code}.jpg")
                        downloaded += 1

                elif media_type == 2:
                    versions = media.get("video_versions", [])
                    if versions:
                        await client.download_file(versions[0]["url"], content_dir / f"{code}.mp4")
                        downloaded += 1

                elif media_type == 8:
                    children = media.get("carousel_media", [])
                    for i, child in enumerate(children):
                        c_type = child.get("media_type")
                        if c_type == 2:
                            v = child.get("video_versions", [])
                            if v:
                                await client.download_file(v[0]["url"], content_dir / f"{code}_{i+1}.mp4")
                        else:
                            c = child.get("image_versions2", {}).get("candidates", [])
                            if c:
                                await client.download_file(c[0]["url"], content_dir / f"{code}_{i+1}.jpg")
                    downloaded += 1

            except Exception as e:
                console.print(f"  [red]Error in saved item {media.get('code', '?')}:[/] {e}")
                errors += 1

            progress.advance(task)
            await asyncio.sleep(random_delay() * 0.3)

    save_metadata(
        {"username": username, "total": len(medias), "saved": all_metadata},
        content_dir / "_saved_index.json",
    )

    console.print(
        f"  [green]{downloaded} saved items downloaded[/]"
        + (f" [yellow]({errors} errors)[/]" if errors else "")
    )
    return {"total": len(medias), "downloaded": downloaded, "errors": errors}
