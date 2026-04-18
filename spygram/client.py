"""Async web API client for Spygram."""

from __future__ import annotations

import asyncio
import random
import httpx
from typing import Any, Optional
from rich.console import Console

IG_APP_ID = "936619743392459"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

console = Console(force_terminal=True)


class SpygramWebClient:
    """Async HTTP client for Instagram Web API."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=30.0,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "X-IG-App-ID": IG_APP_ID,
                "X-ASBD-ID": "129477",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.instagram.com/",
                "Origin": "https://www.instagram.com",
            }
        )
        self.csrf_token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.is_authenticated = False

    def load_cookies(self, cookies: Any) -> None:
        """Load cookies from a CookieJar into the httpx client."""
        self.client.cookies = cookies

        for cookie in self.client.cookies.jar:
            if cookie.name == "csrftoken":
                self.csrf_token = cookie.value
                self.client.headers["X-CSRFToken"] = self.csrf_token
            elif cookie.name == "ds_user_id":
                self.user_id = cookie.value
                self.is_authenticated = True

        if self.is_authenticated:
            console.print(f"  [green]OK[/] Web session loaded (User ID: {self.user_id})")
        else:
            console.print("  [yellow]Warning[/] Cookies were loaded, but no active session was detected")

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self.client.aclose()

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        """Internal request wrapper with rate-limit handling and delays."""
        await asyncio.sleep(random.uniform(1.5, 3.5))

        try:
            response = await self.client.request(method, url, **kwargs)

            if response.status_code == 429:
                console.print("  [red]Warning: rate limit (429). Waiting 60s...[/]")
                await asyncio.sleep(60)
                response = await self.client.request(method, url, **kwargs)

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {}
            console.print(f"  [red]Error: HTTP {e.response.status_code}[/] {e}")
            raise
        except Exception as e:
            console.print(f"  [red]Connection error:[/] {e}")
            raise

    async def get_profile(self, username: str) -> dict:
        """Get user profile info via the web API."""
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        data = await self._request("GET", url)
        return data.get("data", {}).get("user", {})

    async def get_user_id(self, username: str) -> str:
        """Get a user ID from a username."""
        profile = await self.get_profile(username)
        if not profile:
            raise ValueError(f"User {username} not found")
        return profile["id"]

    async def get_user_medias(self, user_id: str, amount: int = 0) -> list[dict]:
        """Get user posts via the feed endpoint with pagination."""
        medias: list[dict] = []
        max_id = ""

        console.print(f"  [dim]Fetching posts (limit: {amount if amount > 0 else 'all'})...[/]")

        while True:
            if amount > 0 and len(medias) >= amount:
                break

            url = f"https://www.instagram.com/api/v1/feed/user/{user_id}/?count=33"
            if max_id:
                url += f"&max_id={max_id}"

            try:
                data = await self._request("GET", url)
                items = data.get("items", [])
                medias.extend(items)

                if not data.get("more_available", False):
                    break

                max_id = data.get("next_max_id", "")
                if not items or not max_id:
                    break

                console.print(f"  [dim]  {len(medias)} posts...[/]")

            except Exception as e:
                console.print(f"  [red]Error paginating posts:[/] {e}")
                break

        return medias[:amount] if amount > 0 else medias

    async def get_highlights_tray(self, user_id: str) -> list[dict]:
        """Get a user's highlights tray via API v1."""
        url = f"https://www.instagram.com/api/v1/highlights/{user_id}/highlights_tray/"
        try:
            data = await self._request("GET", url)
            return data.get("tray", [])
        except Exception as e:
            console.print(f"  [red]Error fetching highlights:[/] {e}")
            return []

    async def download_file(self, url: str, dest_path: str) -> None:
        """Download a file using the authenticated client."""
        async with self.client.stream("GET", url) as response:
            if response.status_code == 200:
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
            else:
                console.print(f"  [red]Download failed ({response.status_code})[/]")

    async def get_stories(self, user_id: str) -> list[dict]:
        """Get active stories via the v1 API."""
        url = f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}"
        try:
            data = await self._request("GET", url)
            items = data.get("reels", {}).get(str(user_id), {}).get("items", [])
            if items:
                return items
        except Exception:
            pass

        url2 = f"https://www.instagram.com/api/v1/feed/user/{user_id}/story/"
        try:
            data = await self._request("GET", url2)
            items = data.get("reel", {}).get("items", [])
            if items:
                return items
        except Exception:
            pass

        return []

    async def get_user_clips(self, user_id: str, amount: int = 0) -> list[dict]:
        """Get user reels via the clips API."""
        url = "https://www.instagram.com/api/v1/clips/user/"
        clips: list[dict] = []
        max_id = ""

        console.print(f"  [dim]Fetching reels (limit: {amount if amount > 0 else 'all'})...[/]")

        while True:
            if amount > 0 and len(clips) >= amount:
                break

            form_data: dict[str, Any] = {
                "target_user_id": user_id,
                "page_size": 50,
                "include_feed_video": "true",
            }
            if max_id:
                form_data["max_id"] = max_id

            try:
                data = await self._request("POST", url, data=form_data)

                items = data.get("items", [])
                for item in items:
                    media = item.get("media")
                    if media:
                        clips.append(media)

                paging = data.get("paging_info", {})
                if not paging.get("more_available"):
                    break

                max_id = paging.get("max_id")
                if not items:
                    break

                console.print(f"  [dim]  {len(clips)} reels...[/]")
                await asyncio.sleep(random.uniform(1.5, 3.0))

            except Exception as e:
                console.print(f"  [red]Error paginating reels:[/] {e}")
                break

        return clips[:amount] if amount > 0 else clips

    async def get_highlight_stories(self, highlight_id: str) -> dict:
        """Get stories inside a highlight."""
        url = f"https://www.instagram.com/api/v1/feed/reels_media/?reel_ids=highlight:{highlight_id}"
        return await self._request("GET", url)

    async def get_saved_medias(self, amount: int = 0) -> list[dict]:
        """Get saved posts for the authenticated user."""
        url = "https://www.instagram.com/api/v1/feed/saved/"
        medias: list[dict] = []

        try:
            data = await self._request("GET", url)
            items = data.get("items", [])
            for item in items:
                media = item.get("media", {})
                if media:
                    medias.append(media)
        except Exception as e:
            console.print(f"  [red]Error fetching saved items:[/] {e}")

        return medias[:amount] if amount > 0 else medias

    async def get_tagged_medias(self, user_id: str, amount: int = 0) -> list[dict]:
        """Get tagged posts for a user via the usertags endpoint with pagination."""
        medias: list[dict] = []
        max_id = ""

        console.print(f"  [dim]Fetching tagged posts (limit: {amount if amount > 0 else 'all'})...[/]")

        while True:
            if amount > 0 and len(medias) >= amount:
                break

            url = f"https://www.instagram.com/api/v1/usertags/{user_id}/feed/"
            if max_id:
                url += f"?max_id={max_id}"

            try:
                data = await self._request("GET", url)
                items = data.get("items",[])
                medias.extend(items)

                if not data.get("more_available", False):
                    break

                max_id = data.get("next_max_id", "")
                if not items or not max_id:
                    break

                console.print(f"  [dim]  {len(medias)} tagged posts...[/]")

            except Exception as e:
                console.print(f"  [red]Error paginating tagged posts:[/] {e}")
                break

        return medias[:amount] if amount > 0 else medias