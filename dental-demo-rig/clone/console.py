"""One shared console, forced to UTF-8.

The rep runs this from a Windows terminal, where stdout defaults to cp1252 and a
single arrow or bullet in a status line raises `UnicodeEncodeError` mid-command.
A crash while cloning reads as "the tool is broken", so encoding is pinned here
rather than left to whatever codepage the machine happens to have.
"""

from __future__ import annotations

import contextlib
import sys

from rich.console import Console

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        # Detached or already-wrapped streams raise; the console still works.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")

console = Console()
