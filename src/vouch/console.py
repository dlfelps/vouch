"""Terminal output that survives consoles which can't print Unicode (cp1252 on Windows)."""

from __future__ import annotations

import sys
from typing import TextIO

_ASCII = str.maketrans({"→": "->", "…": "...", "±": "+/-", "✓": "ok", "✗": "x",
                        "×": "x", "Δ": "d", "≤": "<=", "≥": ">=", "↑": "^", "↓": "v",
                        "—": "--", "–": "-", "·": "|", "“": '"', "”": '"', "’": "'", "●": "*"})


def can_encode(text: str, stream: TextIO | None = None) -> bool:
    stream = stream or sys.stdout
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        text.encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def safe(text: str, stream: TextIO | None = None) -> str:
    """``text`` as-is if ``stream`` can encode it, else with ASCII stand-ins."""
    if can_encode(text, stream):
        return text
    stream = stream or sys.stdout
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        return text.translate(_ASCII).encode(enc, errors="replace").decode(enc)
    except LookupError:
        return text.translate(_ASCII).encode("ascii", errors="replace").decode("ascii")


def out(text: str = "", stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    print(safe(text, stream), file=stream, flush=True)


def err(text: str) -> None:
    out(text, sys.stderr)
