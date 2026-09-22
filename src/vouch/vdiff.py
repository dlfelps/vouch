"""Semantic diff of recorded values between two states of the store (``vouch diff``).

``vouch changes`` answers "which *cited* values moved since someone re-read their
sentences?". This answers "what did that re-run do?": every recorded value, param,
claim, table cell and figure at a git revision, against another revision or the
working tree, with the size and direction of each move.

Revisions are read with git plumbing (``ls-tree``, ``cat-file --batch``); nothing
is checked out. ``[metrics]`` metadata (fmt, unit, better) comes from the current
vouch.toml for both sides, so a key is judged the same way on each.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Iterable

from . import changes as ch
from .config import Config, find_git_root
from .index import Entry, Index, Problem, origin
from .store import SCHEMA
from .values import encode, natural_key

KINDS = ("value", "param", "claim", "table-cell")
SUBFIELDS = ("stat-field", "element")
WORKTREE = "working tree"


class DiffError(RuntimeError):
    pass


@dataclasses.dataclass
class ValueDiff:
    key: str
    cls: str                          # added | removed | changed
    kind: str                         # value | param | claim | table-cell | ... | figure
    group: str                        # "run cifar_vit", "derived", "claims", "table main", ...
    old: str | None = None            # as the paper prints it
    new: str | None = None
    old_raw: Any = None
    new_raw: Any = None
    delta: float | None = None
    rel: float | None = None
    direction: str | None = None      # up | down
    verdict: str | None = None        # better | worse, from the key's `better`
    reasons: list[str] = dataclasses.field(default_factory=list)
    cited: int = 0
    pct: bool = False                 # printed as a percentage: Δ in points

    def delta_text(self) -> str:
        if self.delta is None:
            return ""
        if self.rel is None:
            return f"Δ {self.delta:+.4g}"
        return ch.delta_text(self.delta, self.rel, [r"\%"] if self.pct else [])

    def to_json(self) -> dict:
        return {"key": self.key, "class": self.cls, "kind": self.kind, "group": self.group,
                "old": self.old, "new": self.new, "old_raw": self.old_raw,
                "new_raw": self.new_raw, "delta": self.delta, "relative": self.rel,
                "direction": self.direction, "verdict": self.verdict, "reasons": self.reasons,
                "cited": self.cited}


@dataclasses.dataclass
class DiffResult:
    frm: str
    to: str
    items: list[ValueDiff]
    unchanged: int
    notes: list[str] = dataclasses.field(default_factory=list)

    def count(self, cls: str) -> int:
        return sum(i.cls == cls for i in self.items)

    def to_json(self) -> dict:
        return {"from": self.frm, "to": self.to, "unchanged": self.unchanged,
                "counts": {c: self.count(c) for c in ("changed", "added", "removed")},
                "items": [i.to_json() for i in self.items], "notes": self.notes}


# ---------------------------------------------------------------------------
# a revision of the store, read from git
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    try:
        r = subprocess.run(["git", *args], cwd=root, input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DiffError(f"could not run git: {exc}") from None
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise DiffError(f"git {args[0]} failed: {msg[-1] if msg else r.returncode}")
    return r.stdout


def _cat_files(root: Path, specs: list[str]) -> dict[str, bytes | None]:
    """``{"REV:path": content}`` for many blobs in one ``git cat-file --batch``."""
    if not specs:
        return {}
    raw = _git(root, "cat-file", "--batch", data="".join(s + "\n" for s in specs).encode("utf-8"))
    out: dict[str, bytes | None] = {}
    pos = 0
    for spec in specs:
        nl = raw.index(b"\n", pos)
        header = raw[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) < 3 or header[1] != "blob":       # "<spec> missing"
            out[spec] = None
            continue
        size = int(header[2])
        out[spec] = raw[pos:pos + size]
        pos += size + 1
    return out


def index_at(cfg: Config, rev: str) -> Index:
    """The index as it was at git revision ``rev`` (derived values included)."""
    root = find_git_root(cfg.root)
    if root is None:
        raise DiffError(f"{cfg.root} is not in a git repository; vouch diff compares revisions")
    try:
        _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    except DiffError:
        raise DiffError(f"unknown revision {rev!r}") from None
    try:
        store = cfg.store.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise DiffError(f"the store {cfg.store} is outside the git repository {root}") from None
    listed = _git(root, "ls-tree", "-r", "-z", "--name-only", rev, "--", f"{store}/runs/")
    names = sorted(n for n in listed.decode("utf-8").split("\0")
                   if n.endswith(".json") and "/" not in n[len(store) + 6:])
    derived = f"{store}/derived.json"
    blobs = _cat_files(root, [f"{rev}:{n}" for n in names] + [f"{rev}:{derived}"])

    idx = Index(cfg)
    records = []
    for n in names:
        try:
            rec = json.loads(blobs[f"{rev}:{n}"] or b"")
        except ValueError as exc:
            idx.problems.append(Problem("bad-record", f"{rev}:{n}", f"{rev}:{n}: not valid JSON ({exc})"))
            continue
        if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
            idx.problems.append(Problem("bad-record", f"{rev}:{n}", f"{rev}:{n}: not a {SCHEMA} run record"))
            continue
        records.append((n, rec))
    idx.add_records(records, rel=lambda p: f"{rev}:{p}")
    doc = blobs.get(f"{rev}:{derived}")
    if doc:
        try:
            parsed = json.loads(doc)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("schema") == SCHEMA:
            idx.add_derived(parsed)
    return idx


# ---------------------------------------------------------------------------
# comparing
# ---------------------------------------------------------------------------

def cite_counts(plans, tables: dict) -> dict[str, int]:
    """How many times the papers cite each key; a cited table cites each of its cells."""
    counts: dict[str, int] = {}
    for pl in plans:
        for c in pl.doc.citations:
            counts[c.key] = counts.get(c.key, 0) + 1
            t = tables.get(c.key) if c.kind == "table" else None
            if t is not None:
                for i in range(len(t.rows)):
                    for col in t.columns:
                        ck = t.cell_key(i, col)
                        counts[ck] = counts.get(ck, 0) + 1
    return counts


def _payload(e: Entry) -> tuple[str, Any]:
    if e.kind == "claim":
        p = {"holds": bool(e.raw)}
        if e.extra.get("margin") is not None:
            p["margin"] = e.extra["margin"]
        return "claim", p
    try:
        return encode(e.raw)
    except (TypeError, ValueError):
        return "str", None if e.raw is None else str(e.raw)


def _shown(e: Entry, opts) -> str:
    if e.kind == "claim":
        return "HOLDS" if e.raw else "FALSE"
    from .render import RenderError, render
    try:
        return ch.readable(render(e.raw, e.fmt, unit=e.unit, opts=opts).plain)
    except (RenderError, ValueError, TypeError):
        return str(e.raw)


def _group(e: Entry) -> str:
    if e.kind == "claim":
        return "claims"
    if e.kind == "table-cell" and e.parent:
        return f"table {e.parent}"
    src = origin(e)
    if src.startswith("derive:"):
        return "derived"
    return f"run {src.split(':', 1)[1]}" if src else "other"


def _entries(idx: Index, kinds: tuple[str, ...]) -> dict[str, Entry]:
    return {k: e for k, e in idx.entries.items()
            if e.kind in kinds and not e.extra.get("alias_of")}


def _moved(old: Entry, new: Entry, threshold: float) -> tuple[float | None, float | None, list[str]]:
    """Delta, relative delta and anything that looks like a problem."""
    ot, op = _payload(old)
    nt, np_ = _payload(new)
    if nt == "claim":
        reasons = []
        if op.get("holds") != np_.get("holds"):
            reasons.append("NOW FALSE" if op.get("holds") else "now holds")
        om, nm = op.get("margin"), np_.get("margin")
        if isinstance(om, (int, float)) and isinstance(nm, (int, float)) and om != nm:
            reasons.append(f"margin {om:.3g} → {nm:.3g}")
        return None, None, reasons
    cur = ch.Current(new.key, "value", nt, np_, [], [], origin(new))
    reasons, delta, rel = ch.heuristics({"type": ot, "raw": op, "source": origin(old)}, cur,
                                        threshold)
    a, b = ch.primary(nt, op), ch.primary(nt, np_)
    if ot == nt and len(a) == len(b) == 1 and math.isfinite(a[0]) and math.isfinite(b[0]):
        delta = b[0] - a[0]
        rel = delta / abs(a[0]) if a[0] else None
    return delta, rel, reasons


def compare(cfg: Config, old: Index, new: Index, *, cited: dict[str, int] | None = None,
            subfields: bool = False, keep=lambda key: True) -> tuple[list[ValueDiff], int]:
    """Every difference between two indexes, most important first; and how many keys match.
    ``keep(key)`` limits both to the keys asked about."""
    from .render import Options
    opts = Options.from_config(cfg)
    threshold = float(cfg.get("changes", "rel_threshold", 0.10) or 0.10)
    kinds = KINDS + (SUBFIELDS if subfields else ())
    a, b = _entries(old, kinds), _entries(new, kinds)
    cited = cited or {}
    out: list[ValueDiff] = []
    unchanged = 0
    for key in set(a) | set(b):
        if not keep(key):
            continue
        o, n = a.get(key), b.get(key)
        if o is None:
            out.append(ValueDiff(key, "added", n.kind, _group(n), new=_shown(n, opts),
                                 new_raw=_payload(n)[1], cited=cited.get(key, 0)))
            continue
        if n is None:
            out.append(ValueDiff(key, "removed", o.kind, "removed", old=_shown(o, opts),
                                 old_raw=_payload(o)[1], cited=cited.get(key, 0)))
            continue
        (ot, op), (nt, np_) = _payload(o), _payload(n)
        if ot == nt and ch.same(op, np_):
            unchanged += 1
            continue
        delta, rel, reasons = _moved(o, n, threshold)
        direction = None if delta is None or delta == 0 else ("up" if delta > 0 else "down")
        verdict = None
        if direction and n.better in ("higher", "lower"):
            verdict = "better" if (direction == "up") == (n.better == "higher") else "worse"
        if nt == "claim":
            verdict = ("worse" if "NOW FALSE" in reasons else
                       "better" if "now holds" in reasons else None)
        shown = _shown(n, opts)
        out.append(ValueDiff(key, "changed", n.kind, _group(n), old=_shown(o, opts), new=shown,
                             old_raw=op, new_raw=np_, delta=delta, rel=rel, direction=direction,
                             verdict=verdict, reasons=reasons, cited=cited.get(key, 0),
                             pct=shown.rstrip().endswith("%") or "% " in shown))
    for path in set(old.figures) | set(new.figures):
        if not keep(path):
            continue
        fo, fn = old.figures.get(path), new.figures.get(path)
        if fo is not None and fn is not None and fo.hash == fn.hash:
            unchanged += 1
            continue
        cls = "added" if fo is None else "removed" if fn is None else "changed"
        run = (fn or fo).run
        out.append(ValueDiff(path, cls, "figure", "figures", old=fo and fo.hash, new=fn and fn.hash,
                             reasons=[f"run {run}"], cited=cited.get(path, 0)))
    return sorted(out, key=_order), unchanged


_GROUP_RANK = {"derived": 1, "claims": 2, "removed": 4, "figures": 5}


def _order(d: ValueDiff) -> tuple:
    g = _GROUP_RANK.get(d.group, 3 if d.group.startswith("table ") else 0)
    return (g, natural_key(d.group), not d.cited, d.verdict != "worse", d.cls != "changed",
            natural_key(d.key))


def matches(key: str, patterns: Iterable[str]) -> bool:
    pats = list(patterns)
    return not pats or any(p in key or fnmatch.fnmatchcase(key, p) for p in pats)


def diff(cfg: Config, frm: str = "HEAD", to: str | None = None, *, keys: Iterable[str] = (),
         cited_only: bool = False, subfields: bool = False) -> DiffResult:
    """``frm`` against ``to`` (a revision), or against the working tree when ``to`` is None."""
    from .build import plan
    ctx = plan(cfg, check_env=False, need_paper=False)
    counts = cite_counts(ctx.plans, ctx.idx.tables)
    notes = []
    old = index_at(cfg, frm)
    if to is None:
        new = ctx.idx
        if any(i.check == "out-of-sync" for i in ctx.project_issues):
            notes.append("derived values are out of date; run `vouch build` to include them")
    else:
        new = index_at(cfg, to)
    for p in old.problems + new.problems:
        if p.check == "bad-record":
            notes.append(p.message)
    keys = list(keys)
    items, unchanged = compare(cfg, old, new, cited=counts, subfields=subfields,
                               keep=lambda k: matches(k, keys) and (counts.get(k) or not cited_only))
    return DiffResult(frm, to or WORKTREE, items, unchanged, notes)
