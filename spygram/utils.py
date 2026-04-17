"""Utility helpers for Spygram."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def format_number(n: int) -> str:
    """Format a number with K/M suffixes for display."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def format_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def format_timestamp(dt: Optional[datetime]) -> str:
    """Format a datetime to a readable string."""
    if dt is None:
        return "N/A"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def timestamp_slug(dt: Optional[datetime]) -> str:
    """Generate a filename-safe timestamp slug."""
    if dt is None:
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    return dt.strftime("%Y%m%d_%H%M%S")


def save_metadata(data: dict[str, Any], filepath: Path) -> Path:
    """Save metadata dictionary to a JSON file with UTF-8."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    cleaned = _make_serializable(data)
    filepath.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return filepath


def _make_serializable(obj: Any) -> Any:
    """Recursively convert non-serializable objects to strings."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_make_serializable(item) for item in obj]
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, Path):
        return str(obj)
    elif hasattr(obj, "__dict__"):
        return _make_serializable(obj.__dict__)
    else:
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)


def slugify(text: str) -> str:
    """
    Convert text to a filesystem-safe slug.
    """
    text = text.strip()
    text = re.sub(r'[\\/:*?"<>|]', '', text)
    text = re.sub(r'[\s_]+', '_', text)
    text = text.strip('_')
    return text or "unnamed"


def calculate_dir_size(directory: Path) -> int:
    """Calculate total size of all files in a directory recursively."""
    total = 0
    if directory.exists():
        for f in directory.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total
