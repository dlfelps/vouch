"""Format strings: the grammar, its canonical form, and Python-style aliases.

The canonical grammar is ASCII-only and uses letters where Python uses symbols,
because a format travels through LaTeX: ``%`` starts a comment, non-ASCII is unsafe
inside ``\\csname``, and babel makes ``:;!?`` active in some languages (SPEC §6.1)::

    fmt     := [sign] [","] ["." precision] [type] {suffix}
    sign    := "+"
    type    := "f" | "e" | "g" | "d" | "pct" | "s"
    suffix  := "x" | "u" | "ci" | "to"

Rendering to LaTeX lives in M2; this module only parses and normalizes, which is
all recording needs.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Mapping

TYPES = ("pct", "f", "e", "g", "d", "s")
SUFFIXES = ("x", "u", "ci", "to")

_FMT_RE = re.compile(
    r"(?P<sign>\+)?(?P<group>,)?(?:\.(?P<prec>\d{1,2}))?(?P<type>pct|f|e|g|d|s)?"
    r"(?P<suffixes>(?:x|u|ci|to)*)")
_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


@dataclasses.dataclass(frozen=True)
class Fmt:
    sign: bool = False
    group: bool = False
    precision: int | None = None
    type: str | None = None
    suffixes: tuple[str, ...] = ()

    def canonical(self) -> str:
        return ("+" if self.sign else "") + ("," if self.group else "") + \
            (f".{self.precision}" if self.precision is not None else "") + \
            (self.type or "") + "".join(self.suffixes)


def _alias(spec: str) -> str:
    """Python-style spellings accepted from code: ``.1%``, ``{:.1%}``, ``:.2f``."""
    s = spec.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1]
    if s.startswith(":"):
        s = s[1:]
    if s.endswith("%"):
        s = s[:-1] + "pct"
    return s


def parse(spec: str) -> Fmt:
    """Parse a canonical (or aliased) format; raises ValueError if it isn't one."""
    s = _alias(spec)
    m = _FMT_RE.fullmatch(s)
    if m is None or not s:
        raise ValueError(f"not a vouch format: {spec!r} (e.g. '.1f', '.1pct', ',d', '.2e', '.2fx')")
    suffixes = tuple(re.findall(r"x|u|ci|to", m.group("suffixes") or ""))
    if len(set(suffixes)) != len(suffixes):
        raise ValueError(f"repeated suffix in format {spec!r}")
    prec = m.group("prec")
    return Fmt(sign=bool(m.group("sign")), group=bool(m.group("group")),
               precision=int(prec) if prec is not None else None,
               type=m.group("type"), suffixes=suffixes)


def normalize(spec: str | None, named: Mapping[str, str] | None = None) -> str | None:
    """The canonical spelling of ``spec``, or ``spec`` itself if it names a format.

    A named format is kept by name, so redefining it in ``vouch.toml`` changes how
    every value using it renders without re-running anything.
    """
    if spec is None:
        return None
    spec = str(spec).strip()
    if not spec:
        return None
    if named and spec in named:
        return spec
    if _NAME_RE.fullmatch(spec) and spec not in TYPES and not _FMT_RE.fullmatch(spec):
        raise ValueError(f"unknown named format {spec!r}; define it under [format] named in vouch.toml")
    return parse(spec).canonical()
