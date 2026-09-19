"""The recording API: runs, and what they record (SPEC §4).

A run collects values in memory and writes nothing until it ends cleanly, so a
crash, Ctrl-C or failing ``sys.exit`` leaves the previous record of that id intact.

Bookkeeping must never kill an experiment: every problem with what is recorded
(a bad key, a missing description, a NaN) is a warning on stderr phrased as the
fix. ``VOUCH_STRICT=1`` turns malformed keys into errors.
"""

from __future__ import annotations

import atexit
import csv
import datetime as _dt
import fnmatch
import json
import math
import os
import platform
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from . import console as _console
from . import fmt as _fmt
from .config import Config, ConfigError, discover_root, expand_desc
from .hashing import hash_path
from .store import SCHEMA, ensure_store, write_record
from .tracing import tracker as _tracker
from .units import CLASS, MODULE_KIND, analyze, raw_hash_text, read_source
from .values import (BETTER, Stat, as_mapping, coerce_scalar, encode, encode_cell,
                     encode_param, flatten, is_finite_value, is_number, is_valid_key,
                     join_key, sanitize_key, slug_segment)

_PKG_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))

FIGURE_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps", ".pgf"}
DATA_EXT = {".csv", ".tsv", ".json", ".jsonl", ".parquet", ".feather", ".npz", ".npy",
            ".h5", ".hdf5", ".arrow", ".xlsx"}
MODEL_EXT = {".pt", ".pth", ".ckpt", ".safetensors", ".onnx", ".joblib", ".pkl", ".bin"}


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------

def _warn(msg: str) -> None:
    if os.environ.get("VOUCH_QUIET") != "1":
        _console.err(f"vouch: {msg}")


def _strict() -> bool:
    return os.environ.get("VOUCH_STRICT") == "1"


def _preview(items: list[str], n: int = 5) -> str:
    head = ", ".join(items[:n])
    return head + (f", … (+{len(items) - n} more)" if len(items) > n else "")


def _common_prefix(keys: list[str]) -> str:
    if not keys:
        return ""
    parts = [k.split(".") for k in keys]
    out = []
    for segs in zip(*parts):
        if len(set(segs)) != 1:
            break
        out.append(segs[0])
    return ".".join(out)


# ---------------------------------------------------------------------------
# the project a process records into
# ---------------------------------------------------------------------------

def _detect_main_file() -> str | None:
    """The entry script's path.

    Captured when the project is first touched, because CPython deletes
    ``__main__.__file__`` when the script finishes -- before ``atexit`` handlers,
    which is exactly when an implicit run is written. The loader's ``path``
    survives, so it is the fallback.
    """
    if _main_override:
        return _main_override
    main = sys.modules.get("__main__")
    f = getattr(main, "__file__", None) or getattr(getattr(main, "__loader__", None), "path", None)
    if not f or not str(f).endswith(".py"):
        return None
    return os.path.abspath(f)


_main_override: str | None = None


def set_entry_script(path: str) -> None:
    """The script being run, for ``python -m vouch.exec script.py`` (whose ``__main__``
    is vouch's own module before and after the script runs)."""
    global _main_override
    _main_override = os.path.abspath(path)


def use_project(cfg: Config) -> None:
    """Record into ``cfg``'s project: for records made by the CLI (``vouch run``,
    ``vouch import``), whose own entry script says nothing about the project."""
    global _project_obj
    proj = _Project.__new__(_Project)
    proj.main_file, proj.root, proj.config = None, cfg.root, cfg
    with _project_lock:
        _project_obj = proj


class _Project:
    def __init__(self) -> None:
        self.main_file = _detect_main_file()
        main_file = self.main_file
        start = Path(main_file).resolve().parent if main_file else Path.cwd()
        root, how = discover_root(start)
        self.root = root
        try:
            self.config = Config.load(root)
        except ConfigError as exc:
            _warn(f"{exc}; recording with default settings")
            self.config = Config(root)
        if how in ("git", "cwd"):
            _warn(f"no vouch.toml found; recording into {root.as_posix()}/.vouch "
                  f"(run `vouch init` to configure)")


_project_lock = threading.Lock()
_project_obj: _Project | None = None


def _project() -> _Project:
    global _project_obj
    with _project_lock:
        if _project_obj is None:
            _project_obj = _Project()
        return _project_obj


def _reset_for_tests() -> None:  # pragma: no cover - test helper
    global _project_obj, _explicit, _implicit
    _project_obj = None
    _explicit = None
    _implicit = None
    _written.clear()


def _site() -> str:
    """file:line of the first caller outside the vouch package."""
    f = sys._getframe(1)
    while f is not None and os.path.normcase(os.path.abspath(f.f_code.co_filename)).startswith(_PKG_DIR):
        f = f.f_back
    if f is None:
        return "?"
    return f"{_project().config.rel(f.f_code.co_filename)}:{f.f_lineno}"


# ---------------------------------------------------------------------------
# collected notes, so bulk recording warns once, not once per key
# ---------------------------------------------------------------------------

class _Notes:
    def __init__(self) -> None:
        self.renamed: list[tuple[str, str]] = []
        self.skipped: list[tuple[str, str]] = []
        self.no_desc: list[str] = []
        self.nonfinite: list[str] = []
        self.other: list[str] = []


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

_state_lock = threading.RLock()
_explicit: "Run | None" = None
_implicit: "Run | None" = None
_written: dict[str, str] = {}          # run id -> site that created it, this process
_crashed = False


class Run:
    """One execution of an experiment. Use as ``with vouch.run("id") as run:``."""

    def __init__(self, run_id: str, params: Any = None, prefix: str | None = None,
                 *, _implicit: bool = False) -> None:
        proj = _project()
        self._config: Config = proj.config
        self.id = self._check_id(run_id, "run id")
        self.prefix = self._check_id(prefix, "prefix") if prefix else None
        self.implicit = _implicit
        self._lock = threading.RLock()
        self._values: dict[str, dict] = {}
        self._claims: dict[str, dict] = {}
        self._inputs: dict[str, str | None] = {}
        self._artifacts: dict[str, dict] = {}
        self._tables: dict[str, dict] = {}
        self._params: dict[str, Any] = {}
        self._tracked: dict[str, Any] = {}      # @vouch.track(over=...) calls, combined at the end
        self._started = _dt.datetime.now(_dt.timezone.utc)
        self._t0 = time.perf_counter()
        self._entered = False
        self.closed = False
        self.path: Path | None = None
        if params is not None:
            self.params(params)

    def __repr__(self) -> str:
        return f"<vouch.Run {self.id!r} values={len(self._values)} closed={self.closed}>"

    # -- keys ------------------------------------------------------------------

    @staticmethod
    def _check_id(raw: Any, what: str) -> str:
        if is_valid_key(raw):
            return raw
        fixed = sanitize_key(raw)
        if _strict():
            raise ValueError(f"{raw!r} is not a valid {what}; use letters, digits, _ - and dots, "
                             f"e.g. {fixed!r}")
        _warn(f"{raw!r} is not a valid {what}; using {fixed!r}")
        return fixed

    def _key(self, key: Any) -> str:
        if not is_valid_key(key):
            fixed = sanitize_key(key)
            if _strict():
                raise ValueError(f"{key!r} is not a valid key; use dots/underscores, e.g. {fixed!r}")
            _warn(f"{key!r} is not a valid key; use dots/underscores, e.g. {fixed!r} "
                  f"(recorded as {fixed!r})")
            key = fixed
        return join_key(self.prefix, key)

    # -- values ----------------------------------------------------------------

    def _entry(self, key: str, value: Any, fmt: Any, unit: Any, desc: Any, better: Any,
               site: str, notes: _Notes) -> dict | None:
        try:
            kind, payload = encode(value)
        except TypeError as exc:
            notes.skipped.append((key, str(exc).replace("cannot record a value of type ", "")))
            return None
        entry: dict[str, Any] = {"type": kind, "value": payload, "site": site}
        if fmt is not None:
            try:
                norm = _fmt.normalize(fmt, self._config.named_formats)
            except ValueError as exc:
                notes.other.append(f"{key}: {exc}; format ignored")
                norm = None
            if norm:
                entry["fmt"] = norm
        if unit is not None:
            entry["unit"] = str(unit)
        if desc is not None and str(desc).strip():
            entry["desc"] = str(desc).strip()
        if better is not None:
            if better not in BETTER:
                notes.other.append(f"{key}: better= must be 'higher' or 'lower', not {better!r}")
            elif not is_number(value):
                notes.other.append(f"{key}: better= only applies to numeric values")
            else:
                entry["better"] = better
        if not is_finite_value(value):
            notes.nonfinite.append(key)
        if "desc" not in entry and not self._config.metric_defaults(key).get("desc"):
            notes.no_desc.append(key)
        return entry

    def _put(self, key: str, entry: dict) -> None:
        with self._lock:
            old = self._values.get(key)
            if old is not None:
                _warn(f"{key} recorded twice in run {self.id} ({old['site']}, then "
                      f"{entry['site']}); the last value wins")
            if key in self._claims:
                _warn(f"{key} is both a value and a claim in run {self.id}; keys must be unique")
            self._values[key] = entry

    def record(self, key: str, value: Any, *, fmt: str | None = None, unit: str | None = None,
               desc: str | None = None, better: str | None = None) -> Any:
        """Record one value; returns ``value`` unchanged so it can be used inline."""
        self._check_open()
        site = _site()
        full = self._key(key)
        notes = _Notes()
        entry = self._entry(full, value, fmt, unit, desc, better, site, notes)
        if entry is not None:
            self._put(full, entry)
        for k, why in notes.skipped:
            _warn(f"cannot record {k}: {why} is not a number, string, bool, tuple or Stat")
        for msg in notes.other:
            _warn(msg)
        for k in notes.nonfinite:
            _warn(f"{k} is {value!r}; the run will record it, but check will flag it")
        for k in notes.no_desc:
            _warn(f'{k} has no desc; add desc="..." (or a [metrics] pattern in vouch.toml) '
                  f"so readers and agents can find it")
        return value

    # -- record_all --------------------------------------------------------------

    @staticmethod
    def _pick(arg: Any, full: str, rel: str) -> Any:
        """An argument that is either one value for all keys, or {key-or-glob: value}."""
        if arg is None or not isinstance(arg, Mapping):
            return arg
        for k in (full, rel):
            if k in arg:
                return arg[k]
        best = None
        for pattern, value in arg.items():
            if fnmatch.fnmatchcase(full, pattern) or fnmatch.fnmatchcase(rel, pattern):
                if best is None or len(pattern) > len(best[0]):
                    best = (pattern, value)
        return best[1] if best else None

    @staticmethod
    def _matches(patterns: Any, full: str, rel: str) -> bool:
        if isinstance(patterns, str):
            patterns = [patterns]
        return any(fnmatch.fnmatchcase(full, p) or fnmatch.fnmatchcase(rel, p) for p in patterns)

    def record_all(self, values: Any, *, prefix: str | None = None, fmt: Any = None,
                   desc: Any = None, unit: Any = None, better: Any = None,
                   include: Any = None, exclude: Any = None, row_key: str | None = None,
                   stats: bool = False, table: str | None = None, _site_override: str | None = None
                   ) -> Any:
        """Record every value in a dict, dataclass, DataFrame, rows or results file (SPEC §4.3)."""
        self._check_open()
        site = _site_override or _site()
        notes = _Notes()
        data = values
        if isinstance(values, (str, os.PathLike)):
            path = Path(values)
            self.input(path)
            try:
                data = _load_results_file(path)
            except (OSError, ValueError) as exc:
                _warn(f"record_all could not read {path}: {exc}")
                return values

        base = sanitize_key(prefix) if prefix else None
        if prefix and base != prefix:
            notes.renamed.append((str(prefix), base))

        rows = _as_rows(data)
        if rows is not None:
            flat: dict[str, Any] = {}
            if row_key is None:
                _warn("record_all: tabular data without row_key= is keyed by row position "
                      "(0, 1, …); pass row_key= to name rows")
            for i, row in enumerate(rows):
                if row_key is not None and row_key not in row:
                    notes.skipped.append((f"row {i}", f"no {row_key!r} column"))
                    continue
                rid = slug_segment(row[row_key]) if row_key is not None else str(i)
                for col, v in row.items():
                    if col == row_key:
                        continue
                    flat[f"{rid}.{slug_segment(col)}"] = v
            if table:
                col_fmt = {}
                for col in (rows[0].keys() if rows else ()):
                    if col == row_key:
                        continue
                    rid = slug_segment(rows[0][row_key]) if row_key is not None else "0"
                    rel = f"{rid}.{slug_segment(col)}"
                    f = self._pick(fmt, join_key(self.prefix, base, rel), rel)
                    if f is not None:
                        col_fmt[col] = f
                self.table(table, rows, row_key=row_key, fmt=col_fmt or None, _site_override=site)
        else:
            mapping = as_mapping(data)
            if mapping is None:
                _warn(f"record_all needs a dict, dataclass, DataFrame, list of rows or a results "
                      f"file, not {type(data).__name__}")
                return values
            flat = flatten(mapping)
            if table:
                _warn("record_all: table= needs tabular data (a DataFrame or list of rows)")

        recorded = self._record_flat(base, flat, fmt=fmt, desc=desc, unit=unit, better=better,
                                     include=include, exclude=exclude, stats=stats, site=site,
                                     notes=notes)
        self._report_bulk(notes, recorded)
        return values

    def _record_flat(self, base: str | None, flat: Mapping[str, Any], *, fmt: Any = None,
                     desc: Any = None, unit: Any = None, better: Any = None, include: Any = None,
                     exclude: Any = None, stats: bool = False, site: str, notes: _Notes,
                     extra: Mapping[str, Any] | None = None,
                     call_results: Mapping[str, list] | None = None) -> list[str]:
        """Record {relative key: value} under ``base``; "" as a relative key is ``base`` itself.
        ``extra`` fields (e.g. @vouch.track's call) are added to every entry;
        ``call_results`` adds each call's own result to that entry's call."""
        recorded: list[str] = []
        for rel_raw, v in flat.items():
            rel = sanitize_key(rel_raw) if rel_raw else ""
            if rel != rel_raw:
                notes.renamed.append((rel_raw, rel))
            full = join_key(self.prefix, base, rel)
            if include is not None and not self._matches(include, full, rel):
                continue
            if exclude is not None and self._matches(exclude, full, rel):
                continue
            v = coerce_scalar(v)
            if v is None:
                notes.skipped.append((full, "None"))
                continue
            if isinstance(v, (list, tuple)):
                items = [coerce_scalar(x) for x in v]
                numeric = bool(items) and all(is_number(x) and not isinstance(x, Stat) for x in items)
                if not numeric:
                    notes.skipped.append((full, f"list of {len(items)}"))
                    continue
                v = Stat.of(items) if stats else tuple(items)
            d = self._pick(desc, full, rel)
            if isinstance(d, str) and "{" in d:           # "{-1}", "{0}", "{key}" templates
                d = expand_desc(d, full)
            entry = self._entry(full, v, self._pick(fmt, full, rel), self._pick(unit, full, rel),
                                d, self._pick(better, full, rel), site, notes)
            if entry is not None:
                if extra:
                    entry.update(extra)
                if call_results and rel_raw in call_results and "call" in entry:
                    entry["call"] = {**entry["call"], "results": call_results[rel_raw]}
                self._put(full, entry)
                recorded.append(full)
        return recorded

    def _report_bulk(self, notes: _Notes, recorded: list[str], who: str = "record_all") -> None:
        if notes.renamed:
            _warn(f"{who} renamed {len(notes.renamed)} key(s) to fit the key grammar: "
                  + _preview([f"{a}→{b}" for a, b in notes.renamed]))
        if notes.skipped:
            _warn(f"{who} skipped {len(notes.skipped)} value(s) that are not recordable "
                  f"scalars: " + _preview([f"{k} ({why})" for k, why in notes.skipped]))
        for msg in notes.other:
            _warn(msg)
        if notes.nonfinite:
            _warn(f"{len(notes.nonfinite)} non-finite value(s) recorded (check will flag them): "
                  + _preview(notes.nonfinite))
        if notes.no_desc:
            where = _common_prefix(notes.no_desc)
            _warn(f"{len(notes.no_desc)} key(s) recorded without desc"
                  + (f" ({where}.*)" if where and len(notes.no_desc) > 1 else f" ({notes.no_desc[0]})"
                     if len(notes.no_desc) == 1 else "")
                  + "; add desc= or a [metrics] pattern in vouch.toml")

    # -- claims, inputs, artifacts, tables, params -----------------------------

    def claim(self, key: str, holds: Any, *, desc: str | None = None,
              values: Mapping[str, Any] | None = None) -> bool:
        """Record a claim: a bool, or a Verdict from ``vouch.gt``/``between``/... (which
        also records why it holds and by what margin). Returns whether it holds."""
        from .verdict import Verdict
        self._check_open()
        site = _site()
        full = self._key(key)
        ver = holds if isinstance(holds, Verdict) else None
        verdict = ver.holds if ver is not None else bool(coerce_scalar(holds))
        entry: dict[str, Any] = {"holds": verdict, "site": site}
        if desc:
            entry["desc"] = str(desc)
        else:
            _warn(f'claim {full} has no desc; add desc="..." stating what it asserts')
        if ver is not None:
            if ver.explanation not in ("true", "false"):
                entry["explanation"] = ver.explanation
            if ver.margin is not None and math.isfinite(ver.margin):
                entry["margin"] = round(ver.margin, 6)
        if values:
            enc = {}
            for k, v in dict(values).items():
                try:
                    enc[str(k)] = encode_cell(v)
                except TypeError:
                    enc[str(k)] = str(v)
            entry["values"] = enc
        with self._lock:
            if full in self._values:
                _warn(f"{full} is both a value and a claim in run {self.id}; keys must be unique")
            self._claims[full] = entry
        return verdict

    def input(self, path: str | os.PathLike, *, mode: str | None = None) -> str | None:
        """Declare a file or directory the run reads; hashed now, as it is used."""
        self._check_open()
        mode = mode or self._config.get("freshness", "input_hashing", "content")
        rel = self._config.rel(path)
        try:
            h = hash_path(path, mode)
        except (OSError, ValueError) as exc:
            _warn(f"could not hash input {rel}: {exc}")
            h = None
        if h is None:
            _warn(f"input {rel} does not exist; recorded without a hash")
        with self._lock:
            self._inputs[rel] = h
        return h

    def artifact(self, path: str | os.PathLike, *, kind: str | None = None) -> None:
        """Declare an output file; hashed when the run ends, after it is written."""
        self._check_open()
        self._add_artifact(path, kind, _site())

    def _add_artifact(self, path: str | os.PathLike, kind: str | None, site: str) -> None:
        p = Path(path)
        if kind is None:
            ext = p.suffix.lower()
            kind = ("figure" if ext in FIGURE_EXT else "data" if ext in DATA_EXT
                    else "model" if ext in MODEL_EXT else "file")
        with self._lock:
            self._artifacts[str(p.resolve())] = {"kind": kind, "site": site}

    def table(self, key: str, data: Any, *, columns: list[str] | None = None,
              row_key: str | None = None, fmt: Mapping[str, str] | None = None,
              highlight: Mapping[str, str] | None = None, second: str | None = None,
              midrules: list[int] | None = None, desc: str | None = None,
              _site_override: str | None = None) -> None:
        """Record a table: rows (dicts or lists + columns), a DataFrame, or {row: {col: v}}."""
        self._check_open()
        site = _site_override or _site()
        full = self._key(key)
        entry = table_record(full, data, columns=columns, row_key=row_key, fmt=fmt,
                             highlight=highlight, second=second, midrules=midrules, desc=desc,
                             named_formats=self._config.named_formats, warn=_warn)
        if entry is None:
            return
        entry["site"] = site
        with self._lock:
            if full in self._tables:
                _warn(f"table {full} recorded twice in run {self.id}; the last one wins")
            self._tables[full] = entry

    def params(self, obj: Any) -> None:
        """Set (merge) the run's parameters; each becomes citable as <run>.param.<name>."""
        self._check_open()
        mapping = as_mapping(obj)
        if mapping is None:
            _warn(f"params must be a dict, argparse.Namespace, dataclass or config object, "
                  f"not {type(obj).__name__}")
            return
        with self._lock:
            for k, v in flatten(mapping).items():
                self._params[sanitize_key(k)] = encode_param(v)

    # -- lifecycle ---------------------------------------------------------------

    def _check_open(self) -> None:
        if self.closed:
            raise RuntimeError(f"vouch run {self.id!r} has already ended")

    def __enter__(self) -> "Run":
        global _explicit
        with _state_lock:
            if self._entered:
                raise RuntimeError(f"vouch run {self.id!r} was already used; create a new one")
            if _explicit is not None:
                raise RuntimeError(f"vouch runs cannot nest: {_explicit.id!r} is still active")
            self._entered = True
            _explicit = self
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        global _explicit
        with _state_lock:
            if _explicit is self:
                _explicit = None
        ok = exc_type is None or (issubclass(exc_type, SystemExit) and exc.code in (0, None))
        if ok:
            self.finalize()
        else:
            self.closed = True
            what = exc_type.__name__
            if issubclass(exc_type, SystemExit):
                what = f"sys.exit({exc.code!r})"
            _warn(f"run {self.id} discarded ({what}); its previous record, if any, is unchanged")
        return False

    def finalize(self) -> Path | None:
        """Write the record. Called for you when a ``with`` block ends cleanly."""
        with self._lock:
            if self.closed:
                return self.path
            self.closed = True
        if self._tracked:
            from .track import flush
            flush(self)
        _tracker.report(_warn)
        record = self._build_record()
        proj = _project()
        ensure_store(proj.config.store)
        self.path = write_record(proj.config.store, record)
        with _state_lock:
            if self.id in _written:
                _warn(f"run {self.id} was recorded twice in this process; the second replaces the first")
            _written[self.id] = self.path.as_posix()
        n = len(self._values)
        extra = []
        if self._claims:
            extra.append(f"{len(self._claims)} claim(s)")
        if self._tables:
            extra.append(f"{len(self._tables)} table(s)")
        if self._artifacts:
            extra.append(f"{len(self._artifacts)} artifact(s)")
        _warn(f"recorded run {self.id}: {n} value(s)" + (", " + ", ".join(extra) if extra else "")
              + f" → {proj.config.rel(self.path)}")
        return self.path

    # -- the record ----------------------------------------------------------------

    def _build_record(self, *, code: dict | None = None, command: list[str] | None = None,
                      entry: str | None = None, env: dict | None = None) -> dict:
        """The record to write. ``vouch run`` and ``vouch import`` pass what they know
        better than this process: the code, command and environment of the real run."""
        cfg = self._config
        artifacts = {}
        for abspath, meta in sorted(self._artifacts.items()):
            rel = cfg.rel(abspath)
            h = hash_path(abspath)
            if h is None:
                _warn(f"artifact {rel} was declared but does not exist at the end of run {self.id}")
            artifacts[rel] = {"hash": h, "kind": meta["kind"], "site": meta["site"]}
        main_file = _project().main_file
        first_party = _first_party_files(cfg) if code is None or env is None else []
        return {
            "schema": SCHEMA,
            "run": self.id,
            "status": "complete",
            "entry": entry if code is not None else (cfg.rel(main_file) if main_file else None),
            "command": command if command is not None else _command(cfg),
            "params": dict(sorted(self._params.items())),
            "started": self._started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "duration_s": round(time.perf_counter() - self._t0, 1),
            "git": _git_info(cfg.root),
            "env": env if env is not None else _env_info(cfg, first_party),
            "code": code if code is not None else _code_info(cfg, first_party),
            "inputs": dict(sorted(self._inputs.items())),
            "values": dict(sorted(self._values.items())),
            "claims": dict(sorted(self._claims.items())),
            "artifacts": artifacts,
            "tables": dict(sorted(self._tables.items())),
        }


# ---------------------------------------------------------------------------
# tabular data and results files
# ---------------------------------------------------------------------------

def table_record(full: str, data: Any, *, columns: list[str] | None = None,
                 row_key: str | None = None, fmt: Mapping[str, str] | None = None,
                 highlight: Mapping[str, str] | None = None, second: str | None = None,
                 midrules: list[int] | None = None, desc: str | None = None,
                 named_formats: Mapping[str, str] | None = None, warn=None) -> dict | None:
    """A table as stored: columns, encoded rows and presentation. None if ``data`` isn't
    tabular. Shared by ``run.table`` and ``@vouch.table``."""
    warn = warn or _warn
    rows = _as_rows(data, columns=columns, row_key=row_key)
    if rows is None:
        warn(f"table {full}: expected rows, a DataFrame or {{row: {{col: value}}}}, "
             f"not {type(data).__name__}")
        return None
    cols: list[str] = list(columns) if columns else []
    if not cols:
        for r in rows:
            for c in r:
                if c not in cols:
                    cols.append(c)
    if row_key is not None and row_key not in cols:
        warn(f"table {full}: row_key {row_key!r} is not a column")
        row_key = None
    body = []
    for r in rows:
        cells = []
        for c in cols:
            v = coerce_scalar(r.get(c))
            try:
                cells.append(encode_cell(v))
            except TypeError:
                cells.append(str(v))
        body.append(cells)
    entry: dict[str, Any] = {"columns": [str(c) for c in cols], "rows": body}
    if row_key is not None:
        entry["row_key"] = row_key
    if fmt:
        norm = {}
        for c, f in fmt.items():
            try:
                n = _fmt.normalize(f, dict(named_formats or {}))
            except ValueError as exc:
                warn(f"table {full}, column {c}: {exc}; format ignored")
                continue
            if n:
                norm[str(c)] = n
        if norm:
            entry["fmt"] = norm
    if highlight:
        bad = {c: h for c, h in highlight.items() if h not in ("max", "min", "best")}
        if bad:
            warn(f"table {full}: highlight must be 'max', 'min' or 'best': {bad}")
        hl = {str(c): h for c, h in highlight.items() if c not in bad}
        if hl:
            entry["highlight"] = hl
    if second:
        entry["second"] = str(second)
    if midrules:
        entry["midrules"] = [int(i) for i in midrules]
    if desc:
        entry["desc"] = str(desc)
    return entry


def _is_dataframe(obj: Any) -> bool:
    return all(hasattr(obj, a) for a in ("columns", "iloc", "to_dict", "index"))


def _as_rows(data: Any, *, columns: list[str] | None = None,
             row_key: str | None = None) -> list[dict] | None:
    """Tabular data as a list of dicts, or None if ``data`` isn't tabular."""
    if _is_dataframe(data):
        df = data
        idx = df.index
        default_index = type(idx).__name__ == "RangeIndex" and getattr(idx, "start", 0) == 0
        if not default_index and (row_key is None or row_key not in df.columns):
            name = idx.name or row_key or "index"
            df = df.reset_index()
            if df.columns[0] != name:
                df = df.rename(columns={df.columns[0]: name})
        return [{str(k): v for k, v in r.items()} for r in df.to_dict(orient="records")]
    if isinstance(data, (list, tuple)) and data:
        if all(isinstance(r, Mapping) for r in data):
            return [dict(r) for r in data]
        if columns and all(isinstance(r, (list, tuple)) for r in data):
            return [dict(zip(columns, r)) for r in data]
        return None
    if isinstance(data, Mapping) and data and all(isinstance(v, Mapping) for v in data.values()):
        # {row: {col: value}} is tabular only when asked for as a table (row_key given
        # or called from table()); record_all flattens it as nested keys instead
        if columns is not None or row_key is not None:
            name = row_key or "row"
            return [{name: rk, **dict(v)} for rk, v in data.items()]
    return None


def _coerce_csv(s: str) -> Any:
    if s == "":
        return None
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def _load_results_file(path: Path) -> Any:
    ext = path.suffix.lower()
    if ext == ".json":
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if ext == ".jsonl":
        with open(path, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]
    if ext in (".csv", ".tsv"):
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t" if ext == ".tsv" else ",")
            return [{k: _coerce_csv(v) for k, v in r.items()} for r in reader]
    raise ValueError(f"unsupported results file type {ext!r} (use .json, .jsonl or .csv)")


# ---------------------------------------------------------------------------
# provenance captured at the end of a run
# ---------------------------------------------------------------------------

def _command(cfg: Config) -> list[str]:
    argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    out = ["python"]
    for a in argv[1:]:
        if os.path.isabs(a) and os.path.exists(a):
            out.append(cfg.rel(a))
        else:
            out.append(a)
    return out


def _git(root: Path, *args: str) -> str | None:
    _tracker.internal += 1          # vouch's own subprocess is not one of the run's children
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                           timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        _tracker.internal -= 1
    return r.stdout.strip() if r.returncode == 0 else None


def _git_info(root: Path) -> dict | None:
    commit = _git(root, "rev-parse", "HEAD")
    if not commit:
        return None
    status = _git(root, "status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "dirty": bool(status)}


def _first_party_files(cfg: Config) -> list[str]:
    files = set()
    main_file = _project().main_file
    if main_file and cfg.is_first_party(main_file):
        files.add(main_file)
    # cheap string prefilter first: a process with pandas loaded holds ~1000 modules
    roots = tuple(os.path.normcase(str(r)) for r in cfg.first_party_roots())
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f or not f.endswith(".py"):
            continue
        norm = os.path.normcase(os.path.abspath(f))
        if not norm.startswith(roots) or norm.startswith(_PKG_DIR):
            continue
        if cfg.is_first_party(f):
            files.add(os.path.abspath(f))
    # code that ran without being imported as a module (runpy, exec of a real file)
    for n in list(_tracker.executed):
        f = _tracker.paths.get(n)
        if f and cfg.is_first_party(f):
            files.add(f)
    return sorted(files)


def _code_info(cfg: Config, first_party: list[str]) -> dict:
    """The code units this run depends on (SPEC §8.1-8.2).

    With function-level tracking: every module and class body of each first-party
    file the run loaded, plus the functions it actually executed -- hashed from the
    source *as it was when the run loaded it*. Files that can't be tracked by
    function (imported before tracking started, ...) count whole, and say why.
    """
    gran, why = _tracker.granularity(cfg.get("freshness", "granularity", "function"))
    units: dict[str, str] = {}
    files: dict[str, str | None] = {}
    whole: dict[str, str] = {}
    edited: list[str] = []
    for f in first_party:
        n = os.path.normcase(os.path.abspath(f))
        rel = cfg.rel(f)
        src = _tracker.snapshots.get(n)
        try:
            now = read_source(f)
        except (OSError, SyntaxError, UnicodeDecodeError):
            now = None
        if src is None:
            src = now
        elif now is not None and now != src:
            edited.append(rel)
        if src is None:
            units[f"{rel}::<module>"] = "unparseable"
            continue
        files[rel] = raw_hash_text(src)
        got = analyze(src, rel)
        if got is None:
            units[f"{rel}::<module>"] = "unparseable"
            continue
        hashes, kinds = got
        keep = list(hashes)
        if gran == "function":
            reason = _tracker.whole.get(n)
            ran = _tracker.executed.get(n, set())
            unmapped = sorted(ran - set(hashes))
            if unmapped and not reason:
                reason = f"executed code not found in the source ({', '.join(unmapped[:2])})"
            if reason:
                whole[rel] = reason
            else:
                keep = [q for q in hashes if kinds.get(q) in (MODULE_KIND, CLASS) or q in ran]
        for qual in keep:
            units[f"{rel}::{qual}"] = hashes[qual]
    if edited:
        _warn(f"{', '.join(edited)} changed while the run was in progress; recorded the code that "
              f"ran, so `vouch check` will report this run stale")
    info: dict[str, Any] = {"granularity": gran}
    if why and why != "configured":
        info["why"] = why
    info["units"] = dict(sorted(units.items()))
    info["files"] = dict(sorted(files.items()))
    if whole:
        info["whole_files"] = dict(sorted(whole.items()))
    return info


_dists_cache: dict | None = None


def _packages_distributions() -> dict:
    """import name -> distribution names; scanning every installed dist costs ~0.5 s,
    so it happens once per process however many runs a sweep records."""
    global _dists_cache
    if _dists_cache is None:
        from importlib import metadata
        _dists_cache = metadata.packages_distributions()
    return _dists_cache


def _env_info(cfg: Config, first_party_files: list[str]) -> dict:
    packages: dict[str, str] = {}
    try:
        from importlib import metadata
        dists = _packages_distributions()
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        first_party = {Path(cfg.rel(f)).parts[0].removesuffix(".py") for f in first_party_files}
        tops = {name.split(".", 1)[0] for name in list(sys.modules)}
        for top in sorted(tops):
            if top.startswith("_") or top in stdlib or top in first_party:
                continue
            for dist in dists.get(top, ()):
                try:
                    packages[dist] = metadata.version(dist)
                except metadata.PackageNotFoundError:
                    pass
    except Exception:  # environment introspection must never fail a run
        pass
    return {"python": platform.python_version(), "platform": sysconfig.get_platform(),
            "packages": dict(sorted(packages.items()))}


# ---------------------------------------------------------------------------
# the active run, and the implicit one
# ---------------------------------------------------------------------------

def _default_run_id() -> str:
    if os.environ.get("VOUCH_RUN"):              # set by `vouch run ID -- python ...`
        return sanitize_key(os.environ["VOUCH_RUN"])
    f = _project().main_file
    if not f:
        _warn("recording outside a script (interactive or notebook) with no explicit run; "
              "use `with vouch.run('id'):` -- recording as run 'interactive'")
        return "interactive"
    rel = _project().config.rel(f)
    p = Path(rel)
    stem = p.with_suffix("").as_posix() if not p.is_absolute() else p.stem
    return sanitize_key(stem.replace("/", "."))


def _on_uncaught(exc_type, exc, tb, _prev=[None]) -> None:  # noqa: B006 - holds the previous hook
    global _crashed
    _crashed = True
    prev = _prev[0] or sys.__excepthook__
    prev(exc_type, exc, tb)


def _finalize_implicit() -> None:
    run = _implicit
    if run is None or run.closed:
        return
    if _crashed:
        run.closed = True
        _warn(f"run {run.id} discarded (the script raised an uncaught exception); "
              f"its previous record, if any, is unchanged")
        return
    _tracker.report(_warn, final=True)         # the script is over: unmatched [[track]] rules
    try:
        run.finalize()
    except Exception as exc:  # pragma: no cover - last-chance reporting at exit
        _warn(f"could not write run {run.id}: {exc!r}")


def _implicit_run() -> Run:
    global _implicit
    with _state_lock:
        if _implicit is None or _implicit.closed:
            _implicit = Run(_default_run_id(), _implicit=True)
            if sys.excepthook is not _on_uncaught:
                _on_uncaught.__defaults__[0][0] = sys.excepthook
                sys.excepthook = _on_uncaught
            atexit.register(_finalize_implicit)
        return _implicit


def active_run() -> Run:
    """The run module-level calls act on: the explicit one if inside ``with``, else implicit."""
    from .derive import evaluating
    if evaluating():
        raise RuntimeError("vouch_values.py is evaluated by `vouch build` and must not record "
                           "values; define them with @vouch.derive, @vouch.claim or @vouch.table")
    with _state_lock:
        if _explicit is not None:
            return _explicit
    return _implicit_run()


def run(run_id: str, params: Any = None, prefix: str | None = None) -> Run:
    """An explicit run: ``with vouch.run("cifar_resnet", params=args) as run: ...``."""
    return Run(run_id, params=params, prefix=prefix)


def record(key: str, value: Any, *, fmt: str | None = None, unit: str | None = None,
           desc: str | None = None, better: str | None = None) -> Any:
    return active_run().record(key, value, fmt=fmt, unit=unit, desc=desc, better=better)


def record_all(values: Any, **kwargs: Any) -> Any:
    return active_run().record_all(values, **kwargs)


_MISSING = object()


def claim(key: str, holds: Any = _MISSING, *, desc: str | None = None,
          values: Mapping[str, Any] | None = None, inputs: Any = ()) -> Any:
    """``vouch.claim(key, holds, desc=...)`` records a claim in the active run.

    Without ``holds`` it is a decorator for ``vouch_values.py``: the function computes
    the claim from recorded values and returns a bool or a Verdict (``vouch.gt``, ...).
    """
    if holds is _MISSING:
        from .derive import claim_definition
        return claim_definition(key, desc=desc, inputs=inputs)
    return active_run().claim(key, holds, desc=desc, values=values)


def input(path: str | os.PathLike, *, mode: str | None = None) -> str | None:  # noqa: A001
    return active_run().input(path, mode=mode)


def artifact(path: str | os.PathLike, *, kind: str | None = None) -> None:
    active_run().artifact(path, kind=kind)


def table(key: str, data: Any = _MISSING, **kwargs: Any) -> Any:
    """``vouch.table(key, rows, ...)`` records a table in the active run.

    Without ``rows`` it is a decorator for ``vouch_values.py``: the function assembles
    the table from recorded values (``v[key]``) and returns its rows.
    """
    if data is _MISSING:
        from .derive import table_definition
        return table_definition(key, **kwargs)
    active_run().table(key, data, **kwargs)


def params(obj: Any) -> None:
    active_run().params(obj)


def _figure_saved(path: str, site: str) -> None:
    """A figure written by matplotlib's ``savefig``: an artifact of the active run."""
    from .derive import evaluating
    if evaluating():
        return
    active_run()._add_artifact(path, "figure", site)
