"""Every citable key, gathered from the run records (SPEC §2.1).

A run record stores what was recorded; the index is what the paper can cite:

    values           cifar.resnet.acc
    Stat subfields   cifar.resnet.acc.mean / .std / .n / .ci95 / .min / .max
    parameters       cifar_resnet.param.lr
    claims           cifar.resnet_beats_vit
    tables + cells   main, main.resnet.acc
    figures          by artifact path
    derived          values, claims and tables from vouch_values.py (derived.json)
    aliases          a short name for a key and everything under it

Metadata from ``[metrics]`` in vouch.toml is applied here, at read time, so editing
a pattern re-describes every matching key without re-running anything.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Mapping

from .config import Config
from .store import RecordError, read_record, runs_dir, verify_record
from .values import STAT_FIELDS, Stat, decode, decode_cell, join_key, slug_segment

MAX_ELEMENTS = 64          # a tuple value's elements are citable as key.0, key.1, ... up to this


@dataclasses.dataclass
class Entry:
    key: str
    kind: str                  # value | stat-field | element | param | claim | table | table-cell
    raw: Any                   # decoded: int, float, bool, str, tuple, Stat
    run: str | None = None
    site: str | None = None
    fmt: str | None = None
    unit: str | None = None
    desc: str | None = None
    better: str | None = None
    parent: str | None = None  # stat-field/element: the value's key; table-cell: the table's key
    extra: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Table:
    key: str
    run: str | None
    site: str | None
    columns: list[str]
    rows: list[list[Any]]      # decoded cells
    row_key: str | None = None
    fmt: dict = dataclasses.field(default_factory=dict)
    highlight: dict = dataclasses.field(default_factory=dict)
    second: str | None = None
    midrules: list[int] = dataclasses.field(default_factory=list)
    desc: str | None = None
    derived: dict | None = None          # a @vouch.table: function, deps, runs
    alias_of: str | None = None

    @property
    def origin(self) -> str:
        if self.derived:
            return str(self.derived.get("function", "vouch_values.py"))
        return f"run {self.run}"

    def row_id(self, i: int) -> str:
        if self.row_key is not None and self.row_key in self.columns:
            return slug_segment(self.rows[i][self.columns.index(self.row_key)])
        return str(i)

    def cell_key(self, i: int, col: str) -> str:
        return join_key(self.key, self.row_id(i), slug_segment(col))


@dataclasses.dataclass
class Figure:
    path: str
    run: str
    hash: str | None
    site: str | None


@dataclasses.dataclass
class Problem:
    check: str                 # store-edited | key-conflict | bad-record | alias-target
    subject: str
    message: str


class Index:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.runs: dict[str, dict] = {}
        self.entries: dict[str, Entry] = {}
        self.tables: dict[str, Table] = {}
        self.figures: dict[str, Figure] = {}
        self.problems: list[Problem] = []
        self._sources: dict[str, list[str]] = {}
        self.derived_doc: dict | None = None     # derived.json, once loaded or evaluated
        self.unevaluated: set[str] = set()       # keys vouch_values.py defines, not yet built
        self.pending: dict[str, dict] = {}       # key -> vouch.expect(...), not recorded yet
        self.met: dict[str, dict] = {}           # expectations a run has since fulfilled

    # -- loading ----------------------------------------------------------------

    @classmethod
    def load(cls, cfg: Config) -> "Index":
        idx = cls(cfg)
        for path, rec in _safe_records(cfg.store, idx):
            run = rec.get("run", Path(path).stem)
            if not verify_record(rec):
                idx.problems.append(Problem(
                    "store-edited", f"run:{run}",
                    f"{cfg.rel(path)} does not match its record_hash: it was edited by hand. "
                    f"Re-run the experiment; never edit .vouch/ directly."))
            idx.runs[run] = rec
            idx._add_run(run, rec)
        return idx

    def clone(self) -> "Index":
        """A copy that can take more entries without touching this one."""
        other = Index(self.cfg)
        other.runs = dict(self.runs)
        other.entries = dict(self.entries)
        other.tables = dict(self.tables)
        other.figures = dict(self.figures)
        other.problems = list(self.problems)
        other._sources = {k: list(v) for k, v in self._sources.items()}
        return other

    def conflicts(self) -> list[Problem]:
        """Keys produced by more than one run, definition or alias."""
        return [Problem("key-conflict", key, f"{key} is produced by {len(sources)} sources: "
                        + ", ".join(sources) + "; rename one")
                for key, sources in sorted(self._sources.items()) if len(sources) > 1]

    def _add(self, entry: Entry, source: str) -> None:
        self._sources.setdefault(entry.key, []).append(source)
        if entry.key not in self.entries:          # first (sorted by run id) wins
            self.entries[entry.key] = entry

    def _meta(self, key: str, stored: dict) -> dict:
        """Explicit recorded metadata, falling back to [metrics] patterns."""
        defaults = self.cfg.metric_defaults(key)
        return {f: stored.get(f, defaults.get(f)) for f in ("fmt", "unit", "desc", "better")}

    def _add_run(self, run: str, rec: dict) -> None:
        src = f"run {run}"
        for key, v in (rec.get("values") or {}).items():
            try:
                raw = decode(v["type"], v["value"])
            except (KeyError, ValueError, TypeError):
                self.problems.append(Problem("bad-record", key, f"run {run}: cannot read {key}"))
                continue
            meta = self._meta(key, v)
            extra = {"call": v["call"]} if isinstance(v.get("call"), dict) else {}
            if v.get("timing"):
                extra["timing"] = True            # a tracked call's duration
            self._add(Entry(key, "value", raw, run=run, site=v.get("site"), extra=extra, **meta), src)
            if isinstance(raw, Stat):
                self._add_stat_fields(key, raw, run, v.get("site"), meta, src, extra)
            elif isinstance(raw, tuple) and len(raw) <= MAX_ELEMENTS:
                self._add_elements(key, raw, run, v.get("site"), meta, src, extra,
                                   taken=rec["values"])

        for name, payload in (rec.get("params") or {}).items():
            key = join_key(run, "param", name)
            raw = decode_cell(payload)
            if isinstance(raw, list):
                raw = tuple(raw) if all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                        for x in raw) else ", ".join(map(str, raw))
            if raw is None:
                raw = "None"
            meta = self._meta(key, {})
            meta["desc"] = meta["desc"] or f"parameter {name} of run {run}"
            self._add(Entry(key, "param", raw, run=run, site=None, **meta), src)

        for key, c in (rec.get("claims") or {}).items():
            self._add(Entry(key, "claim", bool(c.get("holds")), run=run, site=c.get("site"),
                            desc=c.get("desc"), extra=_claim_extra(c)), src)

        for key, t in (rec.get("tables") or {}).items():
            self._add_table(key, t, run, t.get("site"), src)

        for path, a in (rec.get("artifacts") or {}).items():
            if a.get("kind") == "figure":
                self.figures[path] = Figure(path, run, a.get("hash"), a.get("site"))

    def _add_table(self, key: str, t: dict, run: str | None, site: str | None, src: str,
                   derived: dict | None = None) -> None:
        over = (self.cfg.data.get("tables") or {}).get(key) or {}    # [tables.<key>] in vouch.toml
        fmt = {**dict(t.get("fmt") or {}), **dict(over.get("fmt") or {})}
        table = Table(key=key, run=run, site=site, columns=list(t.get("columns") or []),
                      rows=[[decode_cell(c) for c in row] for row in t.get("rows") or []],
                      row_key=t.get("row_key"), fmt=fmt,
                      highlight=dict(over.get("highlight", t.get("highlight")) or {}),
                      second=over.get("second", t.get("second")),
                      midrules=list(over.get("midrules", t.get("midrules")) or []),
                      desc=t.get("desc"), derived=derived)
        extra = {"derived": derived} if derived else {}
        self.tables[key] = table
        self._add(Entry(key, "table", None, run=run, site=table.site, desc=table.desc,
                        extra=dict(extra)), src)
        for i, row in enumerate(table.rows):
            for col, cell in zip(table.columns, row):
                ck = table.cell_key(i, col)
                stored = {"fmt": table.fmt.get(col)} if table.fmt.get(col) else {}
                meta = self._meta(ck, stored)
                if not meta["desc"]:
                    meta["desc"] = f"{col} of {table.row_id(i)} in table {key}"
                self._add(Entry(ck, "table-cell", cell, run=run, site=table.site,
                                parent=key, extra={"row": i, "col": col, **extra}, **meta), src)

    # -- derived values (vouch_values.py, through derived.json) --------------------

    def add_derived(self, doc: dict) -> None:
        self.derived_doc = doc
        defs = doc.get("definitions") or {}
        for key in sorted(defs):
            if defs[key].get("kind") == "expect":
                self.add_expect(key, defs[key])
        for key in sorted(defs):
            if defs[key].get("kind") not in ("alias", "expect"):
                self.add_definition(key, defs[key])
        self.add_aliases([(key, defs[key].get("target", ""), defs[key].get("site"))
                          for key in sorted(defs) if defs[key].get("kind") == "alias"])

    def add_aliases(self, aliases: list[tuple[str, str, str | None]]) -> None:
        """Add aliases, each after any alias its target goes through."""
        def through(target: str, short: str) -> bool:
            return target == short or target.startswith(short + ".")
        todo = list(aliases)
        while todo:
            ready = [a for a in todo if not any(through(a[1], b[0]) for b in todo if b is not a)]
            for a in ready or todo[:1]:          # a cycle: add one anyway and report it
                self.add_alias(*a)
                todo.remove(a)

    def add_expect(self, key: str, d: dict) -> None:
        """A key the paper may cite before any run records it (``vouch.expect``)."""
        if self.get(key) is not None or key in self.tables:
            self.met[key] = d
        else:
            self.pending[key] = dict(d)

    def pending_for(self, key: str) -> dict | None:
        """The expectation ``key`` (or the key it is under) waits on, if any."""
        for k, d in self.pending.items():
            if key == k or key.startswith(k + "."):
                return d
        return None

    def add_definition(self, key: str, d: dict) -> None:
        if d.get("pending"):                     # it reads a value that isn't recorded yet
            waits = list(d["pending"])
            producers = [self.pending[w].get("producer") for w in waits
                         if w in self.pending and self.pending[w].get("producer")]
            self.pending.setdefault(key, {"kind": d.get("kind"), "site": d.get("site"),
                                          "function": d.get("function"), "waits": waits,
                                          "producer": "; ".join(dict.fromkeys(producers)) or None})
            return
        info = {"function": d.get("function"), "site": d.get("site"),
                "deps": sorted(d.get("deps") or {}), "runs": list(d.get("runs") or []),
                "inputs": dict(d.get("inputs") or {})}
        src = f"derive {d.get('function')}"
        site = d.get("site")
        kind = d.get("kind")
        if kind == "value":
            for k, v in sorted((d.get("values") or {}).items()):
                try:
                    raw = decode(v["type"], v["value"])
                except (KeyError, ValueError, TypeError):
                    self.problems.append(Problem("bad-record", k, f"derived.json: cannot read {k}"))
                    continue
                meta = self._meta(k, v)
                extra = {"derived": info}
                self._add(Entry(k, "value", raw, run=None, site=site, extra=extra, **meta), src)
                if isinstance(raw, Stat):
                    self._add_stat_fields(k, raw, None, site, meta, src, extra)
                elif isinstance(raw, tuple) and len(raw) <= MAX_ELEMENTS:
                    self._add_elements(k, raw, None, site, meta, src, extra, taken=d["values"])
        elif kind == "claim":
            c = d.get("claim") or {}
            self._add(Entry(key, "claim", bool(c.get("holds")), run=None, site=site,
                            desc=c.get("desc"), extra={**_claim_extra(c), "derived": info}), src)
        elif kind == "table":
            self._add_table(key, d.get("table") or {}, None, site, src, derived=info)

    def add_alias(self, short: str, full: str, site: str | None = None) -> None:
        """``short`` (and ``short.x`` for every ``full.x``) cites what ``full`` cites."""
        def moved(k: str | None) -> str | None:
            if k is not None and (k == full or k.startswith(full + ".")):
                return short + k[len(full):]
            return k
        found = False
        for k in sorted(self.entries):
            if k == full or k.startswith(full + "."):
                e = self.entries[k]
                found = True
                self._add(dataclasses.replace(e, key=moved(k), parent=moved(e.parent),
                                              extra={**e.extra, "alias_of": k}),
                          f"alias {short} -> {full}")
        for k in sorted(self.tables):
            if k == full or k.startswith(full + "."):
                found = True
                self.tables[moved(k)] = dataclasses.replace(self.tables[k], key=moved(k), alias_of=k)
        if not found:
            self.problems.append(Problem("alias-target", short,
                                         f"alias {short} -> {full}: nothing is recorded under "
                                         f"{full}" + (f" ({site})" if site else "")))

    def _add_stat_fields(self, key: str, s: Stat, run: str | None, site: str | None, meta: dict,
                         src: str, extra: dict | None = None) -> None:
        for field in STAT_FIELDS:
            val = s.field(field)
            if val is None:
                continue
            fmt = None if field == "n" else meta["fmt"]
            desc = f"{meta['desc']} ({field})" if meta["desc"] else f"{field} of {key}"
            self._add(Entry(f"{key}.{field}", "stat-field", val, run=run, site=site, fmt=fmt,
                            unit=meta["unit"], desc=desc,
                            better=meta["better"] if field == "mean" else None, parent=key,
                            extra=dict(extra or {})), src)

    def _add_elements(self, key: str, t: tuple, run: str | None, site: str | None, meta: dict,
                      src: str, extra: dict, taken: Mapping) -> None:
        """``key.0``, ``key.1``, ...: each element of a tuple value, citable on its own."""
        for i, val in enumerate(t):
            sub = f"{key}.{i}"
            if sub in taken:                      # a value recorded under that key wins
                continue
            desc = f"{meta['desc']} (element {i})" if meta["desc"] else f"element {i} of {key}"
            self._add(Entry(sub, "element", val, run=run, site=site, fmt=meta["fmt"],
                            unit=meta["unit"], desc=desc, better=meta["better"], parent=key,
                            extra=dict(extra or {})), src)

    # -- queries ----------------------------------------------------------------

    def get(self, key: str) -> Entry | None:
        return self.entries.get(key)

    def run_of(self, entry: Entry) -> dict | None:
        return self.runs.get(entry.run) if entry.run else None

    def awaiting_build(self, key: str) -> bool:
        """True if ``key`` is defined in vouch_values.py but not evaluated yet."""
        return any(key == k or key.startswith(k + ".") for k in self.unevaluated)

    def failed_derivation(self, key: str) -> str | None:
        """Where the definition of ``key`` failed (``file:line``), if vouch_values.py
        defines it but could not compute it; None otherwise."""
        for p in (self.derived_doc or {}).get("problems") or []:
            s = p.get("subject")
            if p.get("check") == "derive-error" and s and (key == s or key.startswith(s + ".")):
                if p.get("file") and p.get("line"):
                    return f"{p['file']}:{p['line']}"
                return p.get("file") or "vouch_values.py"
        return None

    def suggest(self, key: str, n: int = 3) -> list[str]:
        import difflib
        return difflib.get_close_matches(key, list(self.entries), n=n, cutoff=0.6)


def _claim_extra(c: dict) -> dict:
    out = {"values": c.get("values") or {}}
    for k in ("explanation", "margin"):
        if c.get(k) is not None:
            out[k] = c[k]
    return out


def origin(e: Entry) -> str:
    """Where a key's value comes from: ``run:<id>`` or ``derive:<file>::<function>``."""
    d = e.extra.get("derived")
    if d:
        return f"derive:{d.get('function')}"
    return f"run:{e.run}" if e.run else ""


def source_runs(e: Entry) -> list[str]:
    """The runs a value rests on: its own, or every run a derivation read from."""
    d = e.extra.get("derived")
    if d:
        return list(d.get("runs") or [])
    return [e.run] if e.run else []


def _safe_records(store: Path, idx: Index):
    """Every readable record; an unreadable one is a problem, not a crash."""
    d = runs_dir(store)
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.json")):
        try:
            yield path, read_record(path)
        except (RecordError, OSError) as exc:
            idx.problems.append(Problem("bad-record", idx.cfg.rel(path), str(exc)))
