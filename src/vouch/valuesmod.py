"""Where the values modules are, and the keys they define -- read without running them.

Kept apart from ``derived`` (which evaluates them) so that ``vouch check`` and the
edit hook can answer "is this key defined?" without importing the evaluator."""

from __future__ import annotations

import os
from pathlib import Path


def values_modules(cfg) -> list[Path]:
    """The configured values modules that exist."""
    out = []
    for p in cfg.get("python", "values_modules", []) or []:
        path = Path(p) if os.path.isabs(p) else cfg.root / p
        if path.is_file():
            out.append(path.resolve())
    return out


def static_keys(modules: list[Path]) -> set[str]:
    """Keys the values modules define, read from their source without running it:
    the literal first argument of ``derive``/``claim``/``table``/``alias`` calls."""
    import ast
    out: set[str] = set()
    for p in modules:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            first = node.args[0]
            as_definition = name in ("derive", "alias", "expect") or (name in ("claim", "table")
                                                                      and len(node.args) == 1)
            if as_definition and isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(first.value)
    return out


# the calls that record a key from a script: vouch.record("k", value), run.claim("k", holds),
# vouch.table("k", rows) -- two arguments or more (one is a values module's decorator form)
_RECORDERS = ("record", "claim", "table")


def script_keys(cfg) -> dict[str, tuple[str, int]]:
    """Keys the project's scripts record under a literal name, read from their source
    without running anything: key -> (file, line) of the first call that records it.

    A paper may cite a key before the run that records it has happened, a long run still in
    flight, or a line just added. Such a key used to be "not recorded by any run", an error
    and a blocking one in the edit hook; this is what lets vouch call it pending instead.
    Only literal keys count: ``record_all`` prefixes and f-string keys would let a mistyped
    key pass as one that is merely owed. Every first-party ``.py`` file is read, outside the
    excluded directories and the values modules; callers read it only when some cited key
    has no other source.
    """
    import ast
    skip = set(cfg.get("python", "exclude", []) or [])
    values = set(values_modules(cfg))
    out: dict[str, tuple[str, int]] = {}
    for root in cfg.first_party_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith(".")
                                 and d not in ("site-packages", "dist-packages"))
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                path = Path(dirpath) / name
                if path.resolve() in values:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if "vouch" not in text:
                    continue
                try:
                    tree = ast.parse(text)
                except (SyntaxError, ValueError):
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call) or len(node.args) < 2:
                        continue
                    f = node.func
                    fname = (f.attr if isinstance(f, ast.Attribute)
                             else f.id if isinstance(f, ast.Name) else "")
                    first = node.args[0]
                    if (fname in _RECORDERS and isinstance(first, ast.Constant)
                            and isinstance(first.value, str)):
                        out.setdefault(first.value, (cfg.rel(path), node.lineno))
    return out
