"""Posts scraper for Spygram."""

from __future__ import annotations

import asyncio
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

from spygram.client import SpygramWebClient
from spygram.config import get_content_dir, random_delay, DEFAULT_POSTS_LIMIT
from spygram.utils import save_metadata, timestamp_slug

console = Console(force_terminal=True)


async def scrape_posts(
    client: SpygramWebClient,
    username: str,
    user_id: str,
    limit: int = DEFAULT_POSTS_LIMIT,
) -> dict:
    """Scrape user posts with a flat file structure and single index."""
    console.print(f"\n  [bold cyan]Fetching posts for @{username}...[/]")
    content_dir = get_content_dir(username, "posts")

    medias = await client.get_user_medias(user_id, amount=limit)

    if not medias:
        console.print("  [yellow]No posts found[/]")
        return {"total": 0, "downloaded": 0, "errors": 0}

    console.print(f"  [dim]Found {len(medias)} posts[/]")

    downloaded = 0
    errors = 0
    all_metadata = []

    with Progress(
        SpinnerColumn(), TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=25), MofNCompleteColumn(),
        console=console, transient=True,
    ) as progress:
        task = progress.add_task("Downloading posts", total=len(medias))

        for media in medias:
            try:
                code = media.get("code", "")
                media_type = media.get("media_type")
                ts = media.get("taken_at")
                taken_at = datetime.fromtimestamp(ts) if ts else datetime.now()

                caption_text = ""
                caption = media.get("caption")
                if caption:
                    caption_text = caption.get("text", "")

                meta = {
                    "id": str(media.get("pk")),
                    "code": code,
                    "type": {1: "photo", 2: "video", 8: "album"}.get(media_type, "unknown"),
                    "taken_at": str(taken_at),
                    "caption": caption_text,
                    "like_count": media.get("like_count"),
                    "comment_count": media.get("comment_count"),
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
                console.print(f"  [red]Error in post {media.get('code', '?')}:[/] {e}")
                errors += 1

            progress.advance(task)
            await asyncio.sleep(random_delay() * 0.5)

    save_metadata(
        {"username": username, "total": len(medias), "posts": all_metadata},
        content_dir / "_posts_index.json",
    )

    console.print(
        f"  [green]{downloaded} posts downloaded[/]"
        + (f" [yellow]({errors} errors)[/]" if errors else "")
    )
    return {"total": len(medias), "downloaded": downloaded, "errors": errors}
