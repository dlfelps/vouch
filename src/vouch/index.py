"""Every citable key, gathered from the run records (SPEC §2.1).

A run record stores what was recorded; the index is what the paper can cite:

    values           cifar.resnet.acc
    Stat subfields   cifar.resnet.acc.mean / .std / .n / .ci95 / .min / .max
    parameters       cifar_resnet.param.lr
    claims           cifar.resnet_beats_vit
    tables + cells   main, main.resnet.acc
    figures          by artifact path

Metadata from ``[metrics]`` in vouch.toml is applied here, at read time, so editing
a pattern re-describes every matching key without re-running anything.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from .config import Config
from .store import RecordError, read_record, runs_dir, verify_record
from .values import STAT_FIELDS, Stat, decode, decode_cell, join_key, slug_segment


@dataclasses.dataclass
class Entry:
    key: str
    kind: str                  # value | stat-field | param | claim | table | table-cell
    raw: Any                   # decoded: int, float, bool, str, tuple, Stat
    run: str | None = None
    site: str | None = None
    fmt: str | None = None
    unit: str | None = None
    desc: str | None = None
    better: str | None = None
    parent: str | None = None  # stat-field: the Stat's key; table-cell: the table's key
    extra: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Table:
    key: str
    run: str
    site: str | None
    columns: list[str]
    rows: list[list[Any]]      # decoded cells
    row_key: str | None = None
    fmt: dict = dataclasses.field(default_factory=dict)
    highlight: dict = dataclasses.field(default_factory=dict)
    second: str | None = None
    midrules: list[int] = dataclasses.field(default_factory=list)
    desc: str | None = None

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
    check: str                 # store-edited | key-conflict | bad-record
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
        for key, sources in idx._sources.items():
            if len(sources) > 1:
                idx.problems.append(Problem(
                    "key-conflict", key, f"{key} is produced by {len(sources)} sources: "
                    + ", ".join(sources) + "; rename one"))
        return idx

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
            self._add(Entry(key, "value", raw, run=run, site=v.get("site"), extra=extra, **meta), src)
            if isinstance(raw, Stat):
                self._add_stat_fields(key, raw, run, v.get("site"), meta, src, extra)

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
                            desc=c.get("desc"), extra={"values": c.get("values") or {}}), src)

        for key, t in (rec.get("tables") or {}).items():
            table = Table(key=key, run=run, site=t.get("site"), columns=list(t.get("columns") or []),
                          rows=[[decode_cell(c) for c in row] for row in t.get("rows") or []],
                          row_key=t.get("row_key"), fmt=dict(t.get("fmt") or {}),
                          highlight=dict(t.get("highlight") or {}), second=t.get("second"),
                          midrules=list(t.get("midrules") or []), desc=t.get("desc"))
            self.tables[key] = table
            self._add(Entry(key, "table", None, run=run, site=table.site, desc=table.desc), src)
            for i, row in enumerate(table.rows):
                for col, cell in zip(table.columns, row):
                    ck = table.cell_key(i, col)
                    stored = {"fmt": table.fmt.get(col)} if table.fmt.get(col) else {}
                    meta = self._meta(ck, stored)
                    if not meta["desc"]:
                        meta["desc"] = f"{col} of {table.row_id(i)} in table {key}"
                    self._add(Entry(ck, "table-cell", cell, run=run, site=table.site,
                                    parent=key, extra={"row": i, "col": col}, **meta), src)

        for path, a in (rec.get("artifacts") or {}).items():
            if a.get("kind") == "figure":
                self.figures[path] = Figure(path, run, a.get("hash"), a.get("site"))

    def _add_stat_fields(self, key: str, s: Stat, run: str, site: str | None, meta: dict,
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

    # -- queries ----------------------------------------------------------------

    def get(self, key: str) -> Entry | None:
        return self.entries.get(key)

    def run_of(self, entry: Entry) -> dict | None:
        return self.runs.get(entry.run) if entry.run else None

    def suggest(self, key: str, n: int = 3) -> list[str]:
        import difflib
        return difflib.get_close_matches(key, list(self.entries), n=n, cutoff=0.6)


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
