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
