"""Authentication helpers for the web client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
import http.cookiejar

from rich.console import Console
from rich.prompt import Prompt

from spygram.config import ensure_sessions_dir

console = Console()

SESSION_FILE_TEMPLATE = "{username}_web_session.json"

REQUIRED_COOKIES = ["sessionid", "csrftoken", "ds_user_id"]
BROWSER_ORDER = ["chrome", "edge", "firefox", "brave", "opera"]


def _session_path(username: str) -> Path:
    """Return the session file path for a username."""
    return ensure_sessions_dir() / SESSION_FILE_TEMPLATE.format(username=username)


def _dict_to_cookiejar(cookie_dict: dict[str, str]) -> http.cookiejar.CookieJar:
    """Convert a plain cookie dictionary to a CookieJar."""
    cj = http.cookiejar.CookieJar()
    for name, value in cookie_dict.items():
        cookie = http.cookiejar.Cookie(
            version=0, name=name, value=str(value),
            port=None, port_specified=False,
            domain='.instagram.com', domain_specified=True, domain_initial_dot=True,
            path='/', path_specified=True,
            secure=True, expires=None, discard=True,
            comment=None, comment_url=None,
            rest={'HttpOnly': None}, rfc2109=False,
        )
        cj.set_cookie(cookie)
    return cj


def _validate_cookies(cookie_dict: dict[str, str]) -> tuple[bool, list[str]]:
    """Validate required cookies and return missing keys."""
    missing = []
    for key in REQUIRED_COOKIES:
        val = cookie_dict.get(key)
        if not val or not str(val).strip():
            missing.append(key)
    return len(missing) == 0, missing


def save_session(cookies: http.cookiejar.CookieJar, username: str) -> None:
    """Save cookies to a JSON file."""
    path = _session_path(username)
    cookie_dict = {cookie.name: cookie.value for cookie in cookies}
    path.write_text(json.dumps(cookie_dict, indent=2), encoding="utf-8")
    console.print(f"  [dim]Web session saved to[/] [cyan]{path.name}[/]")


def load_session(username: str) -> Optional[http.cookiejar.CookieJar]:
    """Load cookies from a JSON file and validate completeness."""
    path = _session_path(username)
    if not path.exists():
        return None

    try:
        cookie_dict = json.loads(path.read_text(encoding="utf-8"))
        valid, missing = _validate_cookies(cookie_dict)
        if not valid:
            console.print(
                f"  [yellow]Warning: incomplete session (missing: {', '.join(missing)}). "
                f"Delete the session and sign in again.[/]"
            )
            return None

        cj = _dict_to_cookiejar(cookie_dict)
        console.print(f"  [green]OK[/] Web session for {username} loaded")
        return cj
    except Exception:
        return None


def clear_session(username: str) -> None:
    """Delete the saved session file."""
    path = _session_path(username)
    path.unlink(missing_ok=True)
    console.print(f"  [green]OK[/] Session removed for [cyan]{username}[/]")


def get_latest_session() -> Optional[str]:
    """Return the username of the most recently modified session file."""
    sessions_dir = ensure_sessions_dir()
    session_files = list(sessions_dir.glob("*_web_session.json"))
    if not session_files:
        return None

    latest = max(session_files, key=lambda p: p.stat().st_mtime)
    return latest.name.replace("_web_session.json", "")


def login_with_saved_session(username: Optional[str] = None) -> Optional[tuple[str, http.cookiejar.CookieJar]]:
    """Load a saved session or prompt the user to choose one."""
    if username:
        cj = load_session(username)
        if cj:
            return username, cj
        return None

    sessions_dir = ensure_sessions_dir()
    session_files = list(sessions_dir.glob("*_web_session.json"))

    if not session_files:
        console.print("  [yellow]No saved sessions found[/]")
        return None

    console.print("\n  [bold]Available sessions:[/]")
    usernames = []
    for i, sf in enumerate(session_files, 1):
        uname = sf.name.replace("_web_session.json", "")
        usernames.append(uname)
        console.print(f"    [cyan]{i}.[/] {uname}")

    choice = Prompt.ask(
        "\n  [bold]Select a session (number)[/]",
        choices=[str(i) for i in range(1, len(usernames) + 1)],
    )
    username = usernames[int(choice) - 1]

    cj = load_session(username)
    if cj:
        return username, cj
    return None


def login_with_browser_cookies() -> Optional[http.cookiejar.CookieJar]:
    """Extract Instagram cookies from installed browsers using rookiepy."""
    try:
        import rookiepy
    except ImportError:
        console.print(
            "  [red]Error: rookiepy is not installed.[/] Run: pip install rookiepy"
        )
        return None

    browser_fns = {
        "chrome": rookiepy.chrome,
        "edge": rookiepy.edge,
        "firefox": rookiepy.firefox,
        "brave": rookiepy.brave,
        "opera": rookiepy.opera,
    }

    console.print("\n  [dim]Searching for Instagram cookies in browsers...[/]")

    for browser_name in BROWSER_ORDER:
        cookie_fn = browser_fns.get(browser_name)
        if not cookie_fn:
            continue

        try:
            raw_cookies = cookie_fn(domains=[".instagram.com"])

            cookie_dict = {c["name"]: c["value"] for c in raw_cookies if "name" in c and "value" in c}

            valid, missing = _validate_cookies(cookie_dict)
            if valid:
                console.print(f"  [green]OK[/] Valid cookies found in {browser_name.title()}")
                console.print(f"  [dim]  Extracted cookies: {', '.join(cookie_dict.keys())}[/]")
                return _dict_to_cookiejar(cookie_dict)
            else:
                console.print(
                    f"  [yellow]Warning: {browser_name.title()} cookies are incomplete "
                    f"(missing: {', '.join(missing)})[/]"
                )
        except Exception as e:
            console.print(f"  [dim]  {browser_name.title()}: unavailable ({e})[/]")
            continue

    console.print("  [red]Error: no valid Instagram cookies were found in any browser[/]")
    console.print("  [dim]  Make sure you are signed in to Instagram in your browser[/]")
    return None


def login_with_session_id() -> Optional[http.cookiejar.CookieJar]:
    """Manually enter session cookies and construct a CookieJar."""
    console.print("\n  [bold]Enter your Instagram cookies:[/]")
    console.print("  [dim]You can find them in DevTools -> Application -> Cookies -> instagram.com[/]\n")

    session_id = Prompt.ask("  [bold]sessionid[/] [red](required)[/]").strip()
    if not session_id:
        console.print("  [red]Error: sessionid is required[/]")
        return None

    csrf_token = Prompt.ask("  [bold]csrftoken[/] [dim](recommended, press Enter to skip)[/]", default="").strip()
    ds_user_id = Prompt.ask("  [bold]ds_user_id[/] [dim](recommended, press Enter to skip)[/]", default="").strip()

    cookie_dict = {"sessionid": session_id}
    if csrf_token:
        cookie_dict["csrftoken"] = csrf_token
    if ds_user_id:
        cookie_dict["ds_user_id"] = ds_user_id

    valid, missing = _validate_cookies(cookie_dict)
    if not valid:
        console.print(
            f"  [yellow]Warning: recommended cookies are missing: {', '.join(missing)}. "
            f"Some features may fail.[/]"
        )

    return _dict_to_cookiejar(cookie_dict)