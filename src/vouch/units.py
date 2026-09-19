"""Code units and their semantic hashes (SPEC §8.1).

A *unit* is a piece of first-party code a run can depend on:

    path::<module>        module-level statements other than definitions
    path::Class           the class header (name, bases, decorators) and class-level
                          statements other than methods and nested classes
    path::func            a whole top-level definition: decorators, signature, defaults,
                          body, nested functions
    path::Class.method    likewise for methods

The hash is over a canonical serialization of the AST with docstrings removed, so
comments, docstrings, blank lines, formatting and moving code around never change
it, while any change to logic does.

Why not ``ast.dump``: its output is not stable across Python versions (3.13 stopped
printing empty fields), and a record made on 3.12 must still verify on 3.13. The
serializer below omits ``None`` and empty lists, which also absorbs fields that new
Python versions add with empty defaults (``type_params`` in 3.12).
"""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import tokenize
from pathlib import Path

HASH_VERSION = "u1"          # bump if the canonical form ever changes
HASH_LEN = 16

MODULE = "<module>"
MODULE_KIND, CLASS, FUNCTION = "module", "class", "function"

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DOC_OWNERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


# ---------------------------------------------------------------------------
# canonical serialization
# ---------------------------------------------------------------------------

def _ser(node, out: list[str]) -> None:
    if isinstance(node, ast.AST):
        out.append(type(node).__name__)
        out.append("(")
        for name in node._fields:
            value = getattr(node, name, None)
            if value is None or (isinstance(value, list) and not value):
                continue
            if isinstance(node, ast.Constant) and name == "kind":
                continue
            out.append(name)
            out.append("=")
            _ser(value, out)
            out.append(";")
        out.append(")")
    elif isinstance(node, list):
        out.append("[")
        for item in node:
            _ser(item, out)
            out.append(",")
        out.append("]")
    else:
        out.append(f"{type(node).__name__}:{node!r}")


def canonical(node: ast.AST) -> str:
    parts: list[str] = []
    _ser(node, parts)
    return "".join(parts)


def _digest(chunks: list[str]) -> str:
    h = hashlib.sha256(HASH_VERSION.encode())
    for c in chunks:
        h.update(b"\x00")
        h.update(c.encode("utf-8"))
    return h.hexdigest()[:HASH_LEN]


# ---------------------------------------------------------------------------
# docstrings and stubs
# ---------------------------------------------------------------------------

def _is_docstring(stmt: ast.stmt) -> bool:
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str))


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove the docstring of every module, class and function, at any depth."""
    for node in ast.walk(tree):
        if isinstance(node, _DOC_OWNERS) and node.body and _is_docstring(node.body[0]):
            node.body = node.body[1:] or [ast.Pass()]
    return tree


# ---------------------------------------------------------------------------
# unit extraction
# ---------------------------------------------------------------------------

# compound statements whose bodies still belong to the enclosing scope
_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _extract(stmts: list[ast.stmt], units: dict[str, list[str]], prefix: str,
             kinds: dict[str, str]) -> list[ast.stmt]:
    """Return ``stmts`` without their definitions, recording each definition as a unit.

    A definition's decorators, signature and defaults are part of its own unit, so
    leaving it out of the enclosing one loses nothing -- and means that reordering
    functions, or editing one a run never executed, doesn't touch the module's
    hash. Recurses through if/try/with/for/while/match blocks (a conditional
    ``def`` at module level is still a top-level function) but never into
    function bodies.
    """
    out = []
    for stmt in stmts:
        if isinstance(stmt, _DEFS):
            units.setdefault(f"{prefix}{stmt.name}", []).append(canonical(stmt))
            kinds.setdefault(f"{prefix}{stmt.name}", FUNCTION)
        elif isinstance(stmt, ast.ClassDef):
            qual = f"{prefix}{stmt.name}"
            cls = copy.copy(stmt)
            cls.body = _extract(list(stmt.body), units, f"{qual}.", kinds)
            units.setdefault(qual, []).append(canonical(cls))
            kinds[qual] = CLASS
        else:
            stmt = copy.copy(stmt)
            for field in _BLOCK_FIELDS:
                block = getattr(stmt, field, None)
                if isinstance(block, list) and block:
                    new = []
                    for item in block:
                        if isinstance(item, ast.stmt):
                            new.extend(_extract([item], units, prefix, kinds))
                        elif hasattr(item, "body"):          # ExceptHandler, match_case
                            item = copy.copy(item)
                            item.body = _extract(list(item.body), units, prefix, kinds)
                            new.append(item)
                        else:
                            new.append(item)
                    setattr(stmt, field, new)
            out.append(stmt)
    return out


def analyze(source: str, filename: str = "<unknown>") -> tuple[dict[str, str], dict[str, str]] | None:
    """({qualname: hash}, {qualname: kind}) for every unit in ``source``; None if it
    doesn't parse. Kinds are ``module``, ``class`` and ``function``.

    A qualname defined more than once (conditional definitions, a property setter)
    hashes all of its definitions together, in source order.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError):
        return None
    strip_docstrings(tree)
    chunks: dict[str, list[str]] = {}
    kinds: dict[str, str] = {MODULE: MODULE_KIND}
    module = copy.copy(tree)
    module.body = _extract(list(tree.body), chunks, "", kinds)
    chunks[MODULE] = [canonical(module)]
    return {qual: _digest(parts) for qual, parts in chunks.items()}, kinds


def units_from_source(source: str, filename: str = "<unknown>") -> dict[str, str] | None:
    """{qualname: hash} for every unit in ``source``; None if it doesn't parse."""
    got = analyze(source, filename)
    return got[0] if got else None


def read_source(path: str | Path) -> str:
    """Source text honoring PEP 263 coding cookies."""
    with open(path, "rb") as fh:
        raw = fh.read()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding)


def units_from_file(path: str | Path) -> dict[str, str] | None:
    try:
        src = read_source(path)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    return units_from_source(src, str(path))


def raw_hash_text(source: str) -> str:
    """Hash of source text with line endings normalized.

    Used only to tell a cosmetic edit (raw changed, units didn't) from no edit at
    all; normalizing CRLF keeps a Windows checkout from looking edited.
    """
    return hashlib.sha256(source.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:HASH_LEN]


def raw_hash(path: str | Path) -> str | None:
    """``raw_hash_text`` of a file, decoded the way Python decodes source."""
    try:
        return raw_hash_text(read_source(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def unit_of_qualname(qualname: str) -> str:
    """The unit a code object's ``co_qualname`` belongs to (used by function tracking).

    Everything from the first ``<...>`` segment on belongs to what encloses it:
    ``f.<locals>.g`` -> ``f``; ``Model.<lambda>`` (a lambda in a class body) ->
    ``Model``; ``<lambda>``, ``<genexpr>`` or ``<generic parameters of f>`` at module
    level -> ``<module>``.
    """
    kept = []
    for seg in qualname.split("."):
        if seg.startswith("<"):
            break
        kept.append(seg)
    return ".".join(kept) or MODULE
