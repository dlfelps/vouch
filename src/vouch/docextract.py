"""Docstrings read fresh from source, as a lookup-time sanity check.

Independent of ``units.py``'s hashing pass: that pass explicitly strips
docstrings before hashing (SPEC §8.1), so this module parses its own,
unstripped tree instead of reusing ``units.analyze()``'s. Nothing here ever
touches staleness -- a docstring edit still can't fail ``vouch check``.

Best-effort only: a missing file, a syntax error or a missing docstring all
just mean no context to show, never an exception a caller has to handle.
"""

from __future__ import annotations

import ast
from pathlib import Path

MODULE = "<module>"

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _first_line(doc: str) -> str:
    return doc.strip().split("\n\n", 1)[0].split("\n", 1)[0].strip()


def _walk(stmts: list[ast.stmt], out: dict[str, str], prefix: str) -> None:
    for stmt in stmts:
        if isinstance(stmt, (*_DEFS, ast.ClassDef)):
            qual = f"{prefix}{stmt.name}"
            doc = ast.get_docstring(stmt, clean=True)
            if doc:
                out[qual] = _first_line(doc)
            _walk(list(stmt.body), out, f"{qual}.")
            continue
        for field in _BLOCK_FIELDS:
            block = getattr(stmt, field, None)
            if not isinstance(block, list):
                continue
            for item in block:
                if isinstance(item, ast.stmt):
                    _walk([item], out, prefix)
                elif hasattr(item, "body"):          # ExceptHandler, match_case
                    _walk(list(item.body), out, prefix)


def extract(source: str, filename: str = "<unknown>") -> dict[str, str]:
    """{qualname: first line of its docstring} for every documented function,
    class or module in ``source``. ``MODULE`` holds the module docstring.
    ``{}`` if ``source`` doesn't parse."""
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError):
        return {}
    out: dict[str, str] = {}
    mod_doc = ast.get_docstring(tree, clean=True)
    if mod_doc:
        out[MODULE] = _first_line(mod_doc)
    _walk(tree.body, out, "")
    return out


def context_for(fn_ref: str, root: Path, cache: dict[str, dict[str, str]] | None = None) -> str | None:
    """The docstring line for ``fn_ref`` ("path::qualname", as stored in a
    tracked call's ``function`` field), read from the CURRENT source at
    ``root / path`` -- or ``None`` on anything short of success.

    ``cache`` (per-file docstrings, keyed by path) is worth passing in when
    looking up several keys from the same run in one go (e.g. ranked search
    hits); left out, this call alone still pays only one parse.
    """
    if not fn_ref or "::" not in fn_ref:
        return None
    path, qualname = fn_ref.split("::", 1)
    store = {} if cache is None else cache
    docs = store.get(path)
    if docs is None:
        try:
            source = (Path(root) / path).read_text(encoding="utf-8")
        except OSError:
            docs = {}
        else:
            docs = extract(source, path)
        store[path] = docs
    return docs.get(qualname) or None
