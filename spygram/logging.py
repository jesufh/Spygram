"""
spygram.logging
~~~~~~~~~~~~~~~

Compatibility wrapper redirecting to :mod:`spygram.logger`.
"""

from __future__ import annotations

from spygram.logger import (
    SENSITIVE_FIELD_PATTERNS,
    SENSITIVE_KEY_NAMES,
    SensitiveDataFilter,
    mask_secret,
    sanitize_dict,
    sanitize_text,
    setup_logging,
)

__all__ = [
    "SENSITIVE_FIELD_PATTERNS",
    "SENSITIVE_KEY_NAMES",
    "SensitiveDataFilter",
    "mask_secret",
    "sanitize_dict",
    "sanitize_text",
    "setup_logging",
]
