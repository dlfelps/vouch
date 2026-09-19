"""``vouch suggest``: turn numbers already typed into the paper into citations (SPEC §13.2).

Every literal the lint finds is classified:

* **replaceable** -- exactly one recorded value (at the best-ranked kind: a value
  before a param, before a Stat field, before a table cell) prints as the literal;
  ``--apply`` swaps the literal for the ``\\vouch[fmt]{key}`` that prints it, inside
  whatever math delimiters surround it;
* **ambiguous** -- several unrelated values print the same; a person chooses;
* **no source** -- nothing any run produced prints as it: the literal is a typo, a
  stale number, or an invented one. It is never touched.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from .lint import Candidates, Literal, _KIND_RANK, _allowed, find_literals, match, suggestion
from .scan import Document


@dataclasses.dataclass
class Suggestion:
    lit: Literal
    status: str                 # replace | ambiguous | no-source
    snippet: str | None
    keys: list[str]
    near: str | None = None

    def to_json(self) -> dict:
        return {"file": self.lit.file, "line": self.lit.line, "literal": self.lit.text,
                "status": self.status, "replacement": self.snippet, "keys": self.keys,
                "near": self.near}


def suggestions(doc: Document, idx, cfg, only: str | None = None) -> list[Suggestion]:
    rules = [r for r in (cfg.get("lint", "allow", []) or []) if isinstance(r, dict)]
    cands = Candidates(idx)
    rank = {k: r for _, r, k in cands.rows}
    lines = {doc.rel(p): t.split("\n") for p, t in doc.raw.items()}
    out = []
    for lit in find_literals(doc, allow_years=bool(cfg.get("lint", "allow_years", True))):
        if only and lit.file != only:
            continue
        ls = lines.get(lit.file, [])
        if _allowed(lit, rules, ls[lit.line - 1] if 0 < lit.line <= len(ls) else ""):
            continue
        exact, near = match(lit, cands)
        if not exact:
            hint = None
            if near:
                hint = f"{near[0].key} = {near[0].value * near[0].scale:.6g}"
            out.append(Suggestion(lit, "no-source", None, [], hint))
            continue
        best = min(rank.get(m.key, _KIND_RANK["value"]) for m in exact)
        top = [m for m in exact if rank.get(m.key, _KIND_RANK["value"]) == best]
        if len(top) == 1:
            out.append(Suggestion(lit, "replace", suggestion(lit, top[0]), [top[0].key]))
        else:
            out.append(Suggestion(lit, "ambiguous", None, [m.key for m in top]))
    return out


def apply(doc: Document, sugs: list[Suggestion], root: Path) -> dict[Path, int]:
    """Rewrite every replaceable literal; returns {file: replacements}."""
    by_file: dict[str, list[Suggestion]] = {}
    for s in sugs:
        if s.status == "replace" and s.snippet:
            by_file.setdefault(s.lit.file, []).append(s)
    done: dict[Path, int] = {}
    for rel, items in by_file.items():
        path = (root / rel).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        raw_bytes = path.read_bytes()
        crlf = b"\r\n" in raw_bytes
        text = raw_bytes.decode("utf-8").replace("\r\n", "\n")    # the offsets the scan used
        n = 0
        for s in sorted(items, key=lambda x: x.lit.offset, reverse=True):
            written = text[s.lit.offset:s.lit.end]
            if written.strip() != s.lit.text:
                continue                                        # the file moved under us
            text = text[:s.lit.offset] + s.snippet + text[s.lit.end:]
            n += 1
        if n:
            if crlf:
                text = text.replace("\n", "\r\n")
            path.write_bytes(text.encode("utf-8"))
            done[path] = n
    return done
