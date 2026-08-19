"""
spygram.__main__
~~~~~~~~~~~~~~~~

Executable entrypoint wrapper for the Spygram CLI application.
Allows running the package directly via ``python -m spygram``.
"""

from __future__ import annotations

from spygram.cli import main

if __name__ == "__main__":
    main()