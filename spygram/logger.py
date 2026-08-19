"""
spygram.logger
~~~~~~~~~~~~~~

Centralized logging configuration and sensitive data sanitization.

Provides custom log filters to prevent leakage of authentication cookies,
LSD/CSRF security tokens, and proxy credentials across console and file logs.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any

# Sensitive keys and parameter names to redact
SENSITIVE_FIELD_PATTERNS = (
    re.compile(r"(sessionid=)([^;&\s]+)", re.IGNORECASE),
    re.compile(r"(ds_user_id=)([^;&\s]+)", re.IGNORECASE),
    re.compile(r"(csrftoken=)([^;&\s]+)", re.IGNORECASE),
    re.compile(r"(lsd=)([^;&\s]+)", re.IGNORECASE),
    re.compile(r'("sessionid":\s*")([^"]+)(")', re.IGNORECASE),
    re.compile(r'("csrftoken":\s*")([^"]+)(")', re.IGNORECASE),
    re.compile(r'("ds_user_id":\s*")([^"]+)(")', re.IGNORECASE),
    re.compile(r'(X-FB-LSD:\s*)([^\s]+)', re.IGNORECASE),
    re.compile(r'(X-CSRFToken:\s*)([^\s]+)', re.IGNORECASE),
    re.compile(r'(X-IG-WWW-Claim:\s*)([^\s]+)', re.IGNORECASE),
    re.compile(r"(://[^:]+:)([^@]+)(@)", re.IGNORECASE),  # Proxy password redaction
)

SENSITIVE_KEY_NAMES = {
    "sessionid",
    "ds_user_id",
    "csrftoken",
    "x-csrftoken",
    "x-fb-lsd",
    "x-ig-www-claim",
    "lsd",
    "claim",
    "password",
    "secret",
    "token",
}


def mask_secret(value: str, visible_prefix: int = 4, visible_suffix: int = 4) -> str:
    """
    Mask a sensitive string while preserving a few boundary characters for debugging.

    :param value: Secret string to mask.
    :type value: str
    :param visible_prefix: Number of leading characters to keep.
    :type visible_prefix: int
    :param visible_suffix: Number of trailing characters to keep.
    :type visible_suffix: int
    :return: Redacted string representation (e.g., '6821***9a12').
    :rtype: str
    """
    if not value or len(value) <= (visible_prefix + visible_suffix):
        return "***"
    return f"{value[:visible_prefix]}***{value[-visible_suffix:]}"


def sanitize_text(text: str) -> str:
    """
    Scrub sensitive credentials, cookies, and tokens from a text string.

    :param text: Raw text or log message.
    :type text: str
    :return: Sanitized string safe for logging and display.
    :rtype: str
    """
    if not isinstance(text, str):
        text = str(text)

    sanitized = text
    for pattern in SENSITIVE_FIELD_PATTERNS:
        if "://" in pattern.pattern:
            sanitized = pattern.sub(r"\1***\3", sanitized)
        elif '":' in pattern.pattern:
            sanitized = pattern.sub(r'\1***\3', sanitized)
        else:
            sanitized = pattern.sub(r"\1***", sanitized)
    return sanitized


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively sanitize sensitive key-value pairs in a dictionary.

    :param data: Dictionary containing headers, cookies, or payload.
    :type data: dict[str, Any]
    :return: Cleaned copy of the dictionary with redacted secrets.
    :rtype: dict[str, Any]
    """
    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        k_str = str(k).lower()
        if any(s in k_str for s in SENSITIVE_KEY_NAMES):
            cleaned[k] = mask_secret(str(v)) if isinstance(v, str) else "***"
        elif isinstance(v, dict):
            cleaned[k] = sanitize_dict(v)
        elif isinstance(v, list):
            cleaned[k] = [sanitize_dict(item) if isinstance(item, dict) else item for item in v]
        elif isinstance(v, str):
            cleaned[k] = sanitize_text(v)
        else:
            cleaned[k] = v
    return cleaned


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that sanitizes message arguments and strings before emission.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg and isinstance(record.msg, str):
            record.msg = sanitize_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = sanitize_dict(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_dict(a) if isinstance(a, dict)
                    else sanitize_text(a) if isinstance(a, str)
                    else a
                    for a in record.args
                )
        return True


def setup_logging(
    level: int | str = logging.WARNING,
    log_file: Path | str | None = None,
    use_rich: bool = True,
) -> logging.Logger:
    """
    Configure top-level logging for Spygram with sensitive data filtering.

    :param level: Target logging level (e.g. logging.DEBUG, logging.INFO, "DEBUG").
    :type level: int | str
    :param log_file: Optional path to write persistent log output.
    :type log_file: Path | str | None
    :param use_rich: Whether to use RichHandler for formatted console output.
    :type use_rich: bool
    :return: Root 'spygram' logger instance.
    :rtype: logging.Logger
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger("spygram")
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    sensitive_filter = SensitiveDataFilter()

    # 1. Console Handler
    if use_rich:
        try:
            from rich.logging import RichHandler
            console_handler = RichHandler(
                rich_tracebacks=True,
                show_time=True,
                show_path=False,
                tracebacks_show_locals=False,
            )
            console_handler.setFormatter(logging.Formatter("%(message)s"))
        except ImportError:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(
                logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
            )
    else:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        )

    console_handler.setLevel(level)
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    # 2. File Handler (Optional)
    if log_file:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
            )
        )
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)

    return root_logger
