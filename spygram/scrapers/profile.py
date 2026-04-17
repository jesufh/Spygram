"""Profile scraper for Spygram."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from spygram.client import SpygramWebClient
from spygram.config import get_content_dir
from spygram.utils import format_number, save_metadata

console = Console(force_terminal=True)


async def scrape_profile(client: SpygramWebClient, username: str, user_id: str) -> dict:
    """Scrape full profile information for a user."""
    console.print(f"\n  [bold cyan]Fetching profile for @{username}...[/]")

    user_info = await client.get_profile(username)
    if not user_info:
        raise ValueError(f"Could not fetch information for @{username}")

    content_dir = get_content_dir(username, "profile")

    profile_data = {
        "user_id": user_info.get("id"),
        "username": user_info.get("username"),
        "full_name": user_info.get("full_name"),
        "biography": user_info.get("biography"),
        "bio_links": [link.get("url") for link in user_info.get("bio_links", [])],
        "external_url": user_info.get("external_url"),
        "is_private": user_info.get("is_private"),
        "is_verified": user_info.get("is_verified"),
        "is_business": user_info.get("is_business_account"),
        "business_category": user_info.get("business_category_name"),
        "category": user_info.get("category_name"),
        "media_count": user_info.get("edge_owner_to_timeline_media", {}).get("count", 0),
        "follower_count": user_info.get("edge_followed_by", {}).get("count", 0),
        "following_count": user_info.get("edge_follow", {}).get("count", 0),
        "profile_pic_url": user_info.get("profile_pic_url_hd") or user_info.get("profile_pic_url"),
    }

    pic_url = profile_data["profile_pic_url"]
    if pic_url:
        await client.download_file(pic_url, content_dir / "profile_pic.jpg")

    save_metadata(profile_data, content_dir / "profile_info.json")
    _display_profile(profile_data)

    return profile_data


def _display_profile(data: dict) -> None:
    """Display profile info in a styled Rich table."""
    verified_label = "Yes" if data["is_verified"] else "No"
    privacy_label = "Private" if data["is_private"] else "Public"

    table = Table(show_header=False, border_style="dim", pad_edge=True, expand=False)
    table.add_column("Field", style="bold", min_width=16)
    table.add_column("Value")

    table.add_row("Username", f"@{data['username']}")
    table.add_row("Full name", data["full_name"] or "-")
    table.add_row("Bio", (data["biography"] or "-")[:100])
    table.add_row("Privacy", privacy_label)
    table.add_row("Verified", verified_label)
    table.add_row("Posts", format_number(data["media_count"]))
    table.add_row("Followers", format_number(data["follower_count"]))
    table.add_row("Following", format_number(data["following_count"]))

    if data.get("external_url"):
        table.add_row("Link", data["external_url"])
    if data.get("business_category"):
        table.add_row("Category", data["business_category"])

    console.print(Panel(table, title=f"[bold]Profile for @{data['username']}[/]", border_style="cyan"))
