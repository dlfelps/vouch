"""Derived values, claims and tables: ``vouch_values.py`` (SPEC §5).

Numbers computed *from* results -- a difference, a ratio, a best-of, a table
assembled from several runs -- are defined once, in Python, and evaluated by
``vouch build``; never in anyone's head::

    @vouch.derive("toy.gain", fmt=".1f", desc="centroid minus majority, points")
    def gain(v):
        return 100 * (v["toy.centroid.acc.mean"] - v["toy.majority.acc.mean"])

    @vouch.claim("toy.gain_over_10", desc="the gain exceeds 10 points")
    def gain_over_10(v):
        return vouch.gt(v["toy.gain"], 10)

Every ``v[key]`` is logged, so each result knows exactly which values it was
computed from, and through them which runs. ``vouch build`` writes
``.vouch/derived.json``: the results, the values they read (by content hash),
the input files they read, and the semantic hash of the code that ran.

``vouch check`` never imports this code. It re-hashes the code, the values read
and the inputs, and reports ``out-of-sync`` (fix: ``vouch build``) when anything
moved. ``vouch build`` does the same and re-evaluates only then, so an unchanged
project builds without importing anything.
"""

from __future__ import annotations

import csv
import dataclasses
import fnmatch
import hashlib
import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from .config import Config, expand_desc
from .hashing import hash_path
from .issues import Issue
from .store import atomic_write_text, canonical_json, compute_record_hash, verify_record
from .units import units_from_file
from .valuesmod import static_keys, values_modules  # noqa: F401 - re-exported
from .values import (BETTER, coerce_scalar, encode, encode_cell, is_number, is_valid_key,
                     join_key, sanitize_key)

DERIVED = "derived.json"
SCHEMA = "vouch/1"
_PKG_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# definitions, registered while `vouch build` imports the values modules
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Definition:
    kind: str                 # value | claim | table | alias
    key: str
    fn: Callable | None
    opts: dict
    function: str             # vouch_values.py::gain
    site: str                 # vouch_values.py:12 (the decorator line)


class _Registry:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.defs: list[Definition] = []
        self.issues: list[Issue] = []


_active: _Registry | None = None


def evaluating() -> bool:
    """True while `vouch build` imports or evaluates the values modules."""
    return _active is not None


def _code_site(fn: Callable) -> tuple[str, str]:
    code = getattr(fn, "__code__", None)
    qual = getattr(fn, "__qualname__", getattr(fn, "__name__", "?"))
    if code is None or _active is None:
        return f"?::{qual}", "?"
    rel = _active.cfg.rel(code.co_filename)
    return f"{rel}::{qual}", f"{rel}:{code.co_firstlineno}"


def _caller_site() -> tuple[str, str]:
    f = sys._getframe(1)
    while f is not None and os.path.normcase(os.path.abspath(f.f_code.co_filename)).startswith(_PKG_DIR):
        f = f.f_back
    if f is None or _active is None:
        return "?", "?"
    rel = _active.cfg.rel(f.f_code.co_filename)
    return rel, f"{rel}:{f.f_lineno}"


def _register(kind: str, key: Any, fn: Callable | None, opts: dict,
              where: tuple[str, str] | None = None) -> None:
    reg = _active
    if reg is None:          # imported outside `vouch build` (a notebook, a test): inert
        return
    function, site = where or _code_site(fn)
    if not isinstance(key, str) or not is_valid_key(key):
        file, _, line = site.rpartition(":")
        reg.issues.append(Issue("derive-error", "error",
                                f"{key!r} is not a valid key; use letters, digits, _ - and dots, "
                                f"e.g. {sanitize_key(key)!r}", file or None,
                                int(line) if line.isdigit() else None, subject=str(key),
                                fix_kind="edit", fix="rename the key"))
        return
    reg.defs.append(Definition(kind, key, fn, opts, function, site))


def _inputs(inputs: Any) -> list[str]:
    items = [inputs] if isinstance(inputs, (str, os.PathLike)) else list(inputs or ())
    return [os.fspath(p) for p in items]


def derive(key: str, *, fmt: Any = None, unit: Any = None, desc: Any = None, better: Any = None,
           inputs: Any = (), as_frame: bool = False, returns: Any = ()):
    """Define a value computed from recorded ones (``vouch_values.py``)::

        @vouch.derive("cifar.gap", fmt=".1f", unit="points", desc="ResNet minus ViT")
        def gap(v):
            return 100 * (v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"])

    The function gets ``v`` (``v[key]`` reads a value), then one argument per path
    in ``inputs=``. It may return a number, a Stat, a tuple (``returns=`` names the
    elements) or a dict (one key per field, under ``key``).
    """
    from .tracked import check_returns
    names = check_returns(returns, f"@vouch.derive({key!r}, returns=...)")

    def deco(fn: Callable) -> Callable:
        _register("value", key, fn, {"fmt": fmt, "unit": unit, "desc": desc, "better": better,
                                     "inputs": _inputs(inputs), "as_frame": as_frame,
                                     "returns": names})
        return fn
    return deco


def claim_definition(key: str, *, desc: str | None = None, inputs: Any = (),
                     as_frame: bool = False):
    """``@vouch.claim(key, desc=...)``: a claim computed from recorded values. The
    function returns a bool or a Verdict (``vouch.gt``, ``vouch.between``, ...)."""
    def deco(fn: Callable) -> Callable:
        _register("claim", key, fn, {"desc": desc, "inputs": _inputs(inputs), "as_frame": as_frame})
        return fn
    return deco


def table_definition(key: str, *, columns: list[str] | None = None, row_key: str | None = None,
                     fmt: dict | None = None, highlight: dict | None = None,
                     second: str | None = None, midrules: list[int] | None = None,
                     desc: str | None = None, inputs: Any = (), as_frame: bool = False):
    """``@vouch.table(key, columns=[...], ...)``: a table assembled from recorded values.
    The function returns rows: lists (with ``columns=``), dicts, or a DataFrame."""
    def deco(fn: Callable) -> Callable:
        _register("table", key, fn, {"columns": columns, "row_key": row_key, "fmt": fmt,
                                     "highlight": highlight, "second": second,
                                     "midrules": midrules, "desc": desc,
                                     "inputs": _inputs(inputs), "as_frame": as_frame})
        return fn
    return deco


def expect(key: str, *, desc: str, producer: str | None = None, fmt: Any = None,
           unit: Any = None, better: Any = None) -> None:
    """Declare a number the paper needs before any run produces it (SPEC §5.5)::

        vouch.expect("imagenet.convnext.acc", desc="ConvNeXt-T top-1 on ImageNet",
                     producer="python train.py --dataset imagenet --model convnext")

    The paper can cite it at once: the PDF shows ``[pending: key]``, ``vouch todo``
    lists it with its producer, and it resolves by itself once a run records it.
    """
    rel, site = _caller_site()
    _register("expect", key, None, {"desc": desc, "producer": producer, "fmt": fmt, "unit": unit,
                                    "better": better}, where=(f"{rel}::<expect>", site))


def alias(short: str, full: str) -> None:
    """``vouch.alias("resnet", "evaluate.cifar.resnet.lr_0_001")``: cite ``full`` -- and
    every key under it -- by a shorter name: ``\\vouch{resnet.acc}``."""
    rel, site = _caller_site()
    if _active is not None and (not isinstance(full, str) or not is_valid_key(full)):
        file, _, line = site.rpartition(":")
        _active.issues.append(Issue("derive-error", "error", f"alias {short!r}: {full!r} is not a "
                                    f"valid key", file or None, int(line) if line.isdigit() else None,
                                    subject=str(short)))
        return
    _register("alias", short, None, {"target": full}, where=(f"{rel}::<alias>", site))


# ---------------------------------------------------------------------------
# hashing what a result depends on
# ---------------------------------------------------------------------------

def _digest(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:16]


def file_digest(path: Path) -> str:
    """Semantic hash of a Python file: comments, docstrings and formatting don't count."""
    if not path.is_file():
        return "missing"
    units = units_from_file(path)
    if units is None:
        return "unparseable"
    return _digest(sorted(units.items()))


def entry_hash(idx, key: str) -> str | None:
    """Content hash of what ``v[key]`` returns now; None if the key doesn't exist."""
    if key.startswith("keys:"):
        return _digest(_recorded_keys(idx, key[5:]))
    t = idx.tables.get(key)
    if t is not None:
        return _digest(["table", t.columns, [[_enc(c) for c in row] for row in t.rows]])
    e = idx.get(key)
    if e is None:
        return None
    return _digest([e.kind == "claim", _enc(e.raw)])


def _enc(x: Any) -> Any:
    try:
        return list(encode(x))
    except TypeError:
        return ["repr", repr(x)]


def _recorded_keys(idx, pattern: str) -> list[str]:
    return sorted(k for k, e in idx.entries.items()
                  if e.run and e.kind == "value" and "alias_of" not in e.extra
                  and fnmatch.fnmatchcase(k, pattern))


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------

class DeriveCycle(Exception):
    pass


class Pending(Exception):
    """A value a definition read is declared with ``vouch.expect`` but not recorded yet."""

    def __init__(self, keys: list[str]):
        super().__init__(", ".join(keys))
        self.keys = keys


class UnknownKey(KeyError):
    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "unknown key"


class _DepFailed(Exception):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class Values:
    """``v`` in a definition. ``v[key]`` is the value as recorded: a float, a ``Stat``
    (``v["k.mean"]`` for its mean), a bool for a claim, a list of row dicts for a table."""

    def __init__(self, ev: "_Evaluator", rec: dict):
        self._ev, self._rec = ev, rec

    def __getitem__(self, key: str) -> Any:
        return self._ev.read(key, self._rec)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except UnknownKey:
            return default

    def __contains__(self, key: str) -> bool:
        return self._ev.exists(key)

    def keys(self, pattern: str = "*") -> list[str]:
        """Recorded keys (from runs) matching a glob, e.g. ``v.keys("cifar.*.acc")``. The
        list itself is a dependency: a new matching key means re-evaluating."""
        got = _recorded_keys(self._ev.idx, pattern)
        self._rec["deps"][f"keys:{pattern}"] = _digest(got)
        return got


def _load_input(path: Path, as_frame: bool) -> Any:
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        if as_frame:
            import pandas as pd
            return pd.read_csv(path, sep="\t" if ext == ".tsv" else ",")
        with open(path, encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh, delimiter="\t" if ext == ".tsv" else ","))
    if ext == ".json":
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if ext == ".jsonl":
        with open(path, encoding="utf-8") as fh:
            return [json.loads(ln) for ln in fh if ln.strip()]
    return path


class _Evaluator:
    def __init__(self, cfg: Config, idx, defs: list[Definition], issues: list[Issue]):
        self.cfg, self.idx, self.issues = cfg, idx, issues
        self.defs: dict[str, Definition] = {}
        self.aliases: dict[str, Definition] = {}
        self.expects: dict[str, Definition] = {}
        for d in defs:
            if d.kind == "expect":
                if d.key in self.expects:
                    self._issue("derive-error", f"{d.key} is expected twice ({self.expects[d.key].site} "
                                f"and {d.site})", d)
                else:
                    self.expects[d.key] = d
                continue
            table = self.aliases if d.kind == "alias" else self.defs
            if d.key in table or (d.kind == "alias" and d.key in self.defs) or \
                    (d.kind != "alias" and d.key in self.aliases):
                prev = table.get(d.key) or self.defs.get(d.key) or self.aliases.get(d.key)
                self._issue("derive-error", f"{d.key} is defined twice ({prev.site} and {d.site})", d)
                continue
            table[d.key] = d
        self._longest = sorted(self.defs, key=len, reverse=True)
        self._short = sorted(self.aliases, key=len, reverse=True)
        self.results: dict[str, dict] = {}
        self.errors: dict[str, str] = {}
        self.waiting: dict[str, list[str]] = {}          # definition -> the expected keys it needs
        self.stack: list[str] = []
        self.producers = {path: run for run, rec in sorted(idx.runs.items())
                          for path in (rec.get("artifacts") or {})}
        self.external = [str(p).rstrip("/") for p in cfg.get("inputs", "external", []) or []]

    def _issue(self, check: str, message: str, d: Definition, severity: str = "error") -> None:
        file, _, line = d.site.rpartition(":")
        self.issues.append(Issue(check, severity, message, file or None,
                                 int(line) if line.isdigit() else None, subject=d.key))

    # -- reading --------------------------------------------------------------

    def resolve(self, key: str) -> str:
        """``key`` with aliases followed (an alias may name another alias)."""
        for _ in range(8):
            for short in self._short:
                if key == short or key.startswith(short + "."):
                    key = self.aliases[short].opts["target"] + key[len(short):]
                    break
            else:
                return key
        return key

    def owner(self, key: str) -> Definition | None:
        for k in self._longest:
            if key == k or key.startswith(k + "."):
                return self.defs[k]
        return None

    def exists(self, key: str) -> bool:
        target = self.resolve(key)
        return self.owner(target) is not None or self.idx.get(target) is not None \
            or target in self.idx.tables

    def expected(self, key: str) -> str | None:
        for k in self.expects:
            if key == k or key.startswith(k + "."):
                return k
        return None

    def read(self, key: str, rec: dict) -> Any:
        target = self.resolve(key)
        d = self.owner(target)
        if d is not None:
            if d.key == rec["key"]:
                raise DeriveCycle([d.key, d.key])
            self.evaluate(d)
            if d.key in self.errors:
                raise _DepFailed(d.key)
            if d.key in self.waiting:
                rec["deps"][key] = None
                raise Pending(self.waiting[d.key])
        elif self.idx.get(target) is None and target not in self.idx.tables:
            exp = self.expected(target)
            if exp is not None:                  # declared, not recorded yet: wait for it
                rec["deps"][key] = None
                raise Pending([exp])
        if target in self.idx.tables and self.idx.get(target) is not None \
                and self.idx.get(target).kind == "table":
            t = self.idx.tables[target]
            rec["deps"][key] = entry_hash(self.idx, target)
            rec["runs"].update(_runs_of_table(t))
            return [dict(zip(t.columns, row)) for row in t.rows]
        e = self.idx.get(target)
        if e is None:
            sugg = self.idx.suggest(target)
            raise UnknownKey(f"{key!r} is not recorded by any run"
                             + (f" (did you mean {', '.join(sugg)}?)" if sugg else ""))
        rec["deps"][key] = entry_hash(self.idx, target)
        rec["runs"].update(_runs_of(e))
        return e.raw

    # -- evaluating -------------------------------------------------------------

    def evaluate_all(self) -> None:
        for key in sorted(self.defs):
            try:
                self.evaluate(self.defs[key])
            except DeriveCycle:            # already recorded against every member
                pass

    def evaluate(self, d: Definition) -> None:
        if d.key in self.results or d.key in self.errors:
            return
        if d.key in self.stack:
            raise DeriveCycle(self.stack[self.stack.index(d.key):] + [d.key])
        self.stack.append(d.key)
        rec: dict = {"key": d.key, "deps": {}, "runs": set(), "inputs": {}}
        try:
            args = [Values(self, rec)] + self._load_inputs(d, rec)
            result = d.fn(*args)
            doc = self._encode(d, result, rec)
        except Pending as exc:
            self.waiting[d.key] = sorted(set(exc.keys))
            self.results[d.key] = {"kind": d.kind, "function": d.function, "site": d.site,
                                   "pending": self.waiting[d.key],
                                   "deps": dict(sorted(rec["deps"].items())),
                                   "runs": sorted(rec["runs"])}
            self.idx.add_definition(d.key, self.results[d.key])
            self.stack.pop()
            return
        except DeriveCycle as exc:
            cycle = exc.args[0]
            self._fail(d, "derive-cycle", "definitions depend on each other: " + " -> ".join(cycle))
            if cycle[0] != d.key:
                self.stack.pop()
                raise
        except _DepFailed as exc:
            self._fail(d, "derive-error", f"{d.key} reads {exc.key}, which could not be computed")
        except UnknownKey as exc:
            self._fail(d, "derive-error", f"{d.key} reads {exc}")
        except Exception as exc:
            self._fail(d, "derive-error", f"{d.key}: {type(exc).__name__}: {exc}{self._at(exc)}")
        else:
            self.results[d.key] = doc
            self.idx.add_definition(d.key, doc)
        self.stack.pop()

    def _at(self, exc: BaseException) -> str:
        frames = [f for f in traceback.extract_tb(exc.__traceback__)
                  if self.cfg.is_first_party(f.filename)
                  and not os.path.normcase(os.path.abspath(f.filename)).startswith(_PKG_DIR)]
        if not frames:
            return ""
        f = frames[-1]
        return f" (at {self.cfg.rel(f.filename)}:{f.lineno})"

    def _fail(self, d: Definition, check: str, message: str) -> None:
        self.errors[d.key] = message
        self._issue(check, message, d)

    def _load_inputs(self, d: Definition, rec: dict) -> list[Any]:
        out = []
        for p in d.opts.get("inputs") or []:
            path = Path(p) if os.path.isabs(p) else self.cfg.root / p
            rel = self.cfg.rel(path)
            if not path.exists():
                raise FileNotFoundError(f"input {rel} does not exist")
            rec["inputs"][rel] = hash_path(path)
            run = self.producers.get(rel)
            if run:
                rec["runs"].add(run)
            elif not any(rel == x or rel.startswith(x + "/") for x in self.external):
                self._issue("untracked-input", f"{d.key} reads {rel}, which no run produced "
                            f"(produce it in a run with vouch.artifact, or list it under "
                            f"[inputs] external in vouch.toml)", d)
            out.append(_load_input(path, bool(d.opts.get("as_frame"))))
        return out

    def _encode(self, d: Definition, result: Any, rec: dict) -> dict:
        doc: dict[str, Any] = {"kind": d.kind, "function": d.function, "site": d.site}
        if d.kind == "value":
            doc["values"] = self._encode_values(d, result)
        elif d.kind == "claim":
            doc["claim"] = self._encode_claim(d, result, rec)
        else:
            doc["table"] = self._encode_table(d, result)
        doc["deps"] = dict(sorted(rec["deps"].items()))
        doc["runs"] = sorted(rec["runs"])
        if rec["inputs"]:
            doc["inputs"] = dict(sorted(rec["inputs"].items()))
        return doc

    def _encode_values(self, d: Definition, result: Any) -> dict:
        from . import fmt as _fmt
        from .api import Run
        from .tracked import _flat_result
        flat = _flat_result(result, d.opts.get("returns") or ())
        if flat is None:
            raise TypeError(f"returned a {type(result).__name__}; return a number, a Stat, a "
                            f"tuple or a dict of them")
        out: dict[str, dict] = {}
        for rel_raw, v in flat.items():
            rel = sanitize_key(rel_raw) if rel_raw else ""
            full = join_key(d.key, rel) if rel else d.key
            v = coerce_scalar(v)
            if isinstance(v, list) and v and all(is_number(x) for x in v):
                v = tuple(v)
            if v is None:
                raise TypeError(f"{full} is None")
            kind, payload = encode(v)
            entry: dict[str, Any] = {"type": kind, "value": payload}
            f = Run._pick(d.opts.get("fmt"), full, rel)
            if f is not None:
                try:
                    norm = _fmt.normalize(f, self.cfg.named_formats)
                except ValueError as exc:
                    self._issue("format", f"{full}: {exc}; format ignored", d, "warning")
                    norm = None
                if norm:
                    entry["fmt"] = norm
            unit = Run._pick(d.opts.get("unit"), full, rel)
            if unit is not None:
                entry["unit"] = str(unit)
            desc = Run._pick(d.opts.get("desc"), full, rel)
            if isinstance(desc, str) and desc.strip():
                entry["desc"] = expand_desc(desc, full).strip() if "{" in desc else desc.strip()
            better = Run._pick(d.opts.get("better"), full, rel)
            if better is not None:
                if better in BETTER and is_number(v):
                    entry["better"] = better
                else:
                    self._issue("derive-error", f"{full}: better= must be 'higher' or 'lower' "
                                f"on a number", d, "warning")
            out[full] = entry
        return dict(sorted(out.items()))

    def _encode_claim(self, d: Definition, result: Any, rec: dict) -> dict:
        from .verdict import as_verdict
        ver = as_verdict(result)
        if ver is None:
            raise TypeError(f"a claim returns a bool or a Verdict (vouch.gt, vouch.between, ...), "
                            f"not {type(result).__name__}")
        c: dict[str, Any] = {"holds": ver.holds}
        if d.opts.get("desc"):
            c["desc"] = str(d.opts["desc"])
        else:
            self._issue("no-description", f"claim {d.key} has no desc; add desc=\"...\" stating "
                        f"what it asserts", d, "warning")
        if ver.explanation not in ("true", "false"):
            c["explanation"] = ver.explanation
        if ver.margin is not None and ver.margin == ver.margin and abs(ver.margin) != float("inf"):
            c["margin"] = round(ver.margin, 6)
        vals = {}
        for k in list(rec["deps"])[:8]:
            e = self.idx.get(self.resolve(k))
            if e is not None and e.kind != "table" and not k.startswith("keys:"):
                try:
                    vals[k] = encode_cell(e.raw)
                except TypeError:
                    vals[k] = str(e.raw)
        if vals:
            c["values"] = vals
        return c

    def _encode_table(self, d: Definition, result: Any) -> dict:
        from .api import table_record
        problems: list[str] = []
        o = d.opts
        entry = table_record(d.key, result, columns=o.get("columns"), row_key=o.get("row_key"),
                             fmt=o.get("fmt"), highlight=o.get("highlight"), second=o.get("second"),
                             midrules=o.get("midrules"), desc=o.get("desc"),
                             named_formats=self.cfg.named_formats, warn=problems.append)
        for p in problems:
            self._issue("table", p, d, "warning")
        if entry is None:
            raise TypeError(f"returned a {type(result).__name__}; a table returns rows (lists "
                            f"with columns=, dicts) or a DataFrame")
        return entry


def _runs_of(e) -> set[str]:
    d = e.extra.get("derived")
    if d:
        return set(d.get("runs") or [])
    return {e.run} if e.run else set()


def _runs_of_table(t) -> set[str]:
    if t.derived:
        return set(t.derived.get("runs") or [])
    return {t.run} if t.run else set()


# ---------------------------------------------------------------------------
# importing the values modules
# ---------------------------------------------------------------------------

def _import(cfg: Config, path: Path, issues: list[Issue]) -> None:
    rel = cfg.rel(path)
    name = path.stem
    old = sys.modules.get(name)
    if old is not None:
        f = getattr(old, "__file__", None)
        if f and Path(f).resolve() == path:
            del sys.modules[name]
        else:
            name = f"_vouch_values_{_digest(str(path))}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except (Exception, SystemExit) as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__)
                  if Path(f.filename).resolve() == path]
        line = frames[-1].lineno if frames else None
        issues.append(Issue("derive-error", "error", f"{rel} failed to import: "
                            f"{type(exc).__name__}: {exc}", rel, line, subject=rel,
                            fix_kind="edit", fix=f"fix {rel}"))


def _first_party_code(cfg: Config, modules: list[Path]) -> dict[str, str]:
    files = {p.resolve() for p in modules}
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not f or not f.endswith(".py"):
            continue
        if os.path.normcase(os.path.abspath(f)).startswith(_PKG_DIR):
            continue
        if cfg.is_first_party(f):
            files.add(Path(f).resolve())
    return {cfg.rel(f): file_digest(f) for f in sorted(files)}


def evaluate(cfg: Config, idx, modules: list[Path]) -> tuple[dict, list[Issue]]:
    """Import the values modules, evaluate every definition into ``idx``; the document
    to write to derived.json, and the problems found."""
    global _active
    reg = _Registry(cfg)
    before = set(sys.modules)
    saved_path = list(sys.path)
    for d in dict.fromkeys([str(p.parent) for p in modules] + [str(cfg.root)]):
        if d not in sys.path:
            sys.path.insert(0, d)
    _active = reg
    try:
        for p in modules:
            _import(cfg, p, reg.issues)
        ev = _Evaluator(cfg, idx, reg.defs, reg.issues)
        for key, x in ev.expects.items():          # known before anything reads them
            idx.add_expect(key, {"kind": "expect", "site": x.site, "desc": x.opts.get("desc"),
                                 "producer": x.opts.get("producer")})
        ev.evaluate_all()
        code = _first_party_code(cfg, modules)
    finally:
        _active = None
        sys.path[:] = saved_path
        for name in set(sys.modules) - before:       # the next build imports them afresh
            f = getattr(sys.modules.get(name), "__file__", None)
            if f is not None and cfg.is_first_party(f):
                sys.modules.pop(name, None)
    defs: dict[str, dict] = dict(ev.results)
    for key, a in ev.aliases.items():
        defs[key] = {"kind": "alias", "target": a.opts["target"], "site": a.site}
    for key, x in ev.expects.items():
        entry = {"kind": "expect", "site": x.site, "desc": x.opts.get("desc")}
        for f in ("producer", "fmt", "unit", "better"):
            if x.opts.get(f) is not None:
                entry[f] = x.opts[f]
        defs[key] = entry
    idx.add_aliases([(key, a.opts["target"], a.site) for key, a in sorted(ev.aliases.items())])
    problems = sorted((_issue_json(i) for i in reg.issues),
                      key=lambda x: (x.get("file") or "", x.get("line") or 0, x["message"]))
    doc = {"schema": SCHEMA, "modules": [cfg.rel(p) for p in modules], "code": code,
           "definitions": dict(sorted(defs.items())), "problems": problems}
    return doc, _issues(doc)


def _issue_json(i: Issue) -> dict:
    out = {"check": i.check, "severity": i.severity, "message": i.message}
    for k in ("file", "line", "subject"):
        if getattr(i, k) is not None:
            out[k] = getattr(i, k)
    return out


def _issues(doc: dict) -> list[Issue]:
    """The problems found when derived.json was evaluated: reported by every build and
    check until the definitions are fixed."""
    out = []
    for p in doc.get("problems") or []:
        sev = p.get("severity", "error")
        fix = "fix the definition, then vouch build" if sev == "error" else None
        out.append(Issue(p.get("check", "derive-error"), sev, p.get("message", ""), p.get("file"),
                         p.get("line"), subject=p.get("subject"), fix=fix, fix_kind="edit"))
    return out


# ---------------------------------------------------------------------------
# derived.json
# ---------------------------------------------------------------------------

def path_of(cfg: Config) -> Path:
    return cfg.store / DERIVED


def load(cfg: Config) -> dict | None:
    p = path_of(cfg)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema": "unreadable"}
    return doc if isinstance(doc, dict) else {"schema": "unreadable"}


def dumps(doc: dict) -> str:
    """Deterministic, one definition per line: its diff reads like the values it holds."""
    doc = dict(doc)
    doc.pop("record_hash", None)
    doc["record_hash"] = compute_record_hash(doc)
    j = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)  # noqa: E731
    lines = ["{", f'  "schema": {j(doc["schema"])},', f'  "modules": {j(doc["modules"])},',
             f'  "code": {j(doc["code"])},']
    items = list(doc["definitions"].items())
    lines.append('  "definitions": {' + ("" if items else "},"))
    for i, (k, v) in enumerate(items):
        lines.append(f"    {j(k)}: {j(v)}" + ("," if i < len(items) - 1 else ""))
    if items:
        lines.append("  },")
    probs = doc.get("problems") or []
    lines.append('  "problems": [' + ("" if probs else "],"))
    for i, v in enumerate(probs):
        lines.append(f"    {j(v)}" + ("," if i < len(probs) - 1 else ""))
    if probs:
        lines.append("  ],")
    lines.append(f'  "record_hash": {j(doc["record_hash"])}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def stale_probe(cfg: Config, idx, doc: dict, modules: list[Path]) -> bool:
    probe = idx.clone()
    probe.add_derived(doc)
    return bool(stale_reasons(cfg, probe, doc, modules))


def stale_reasons(cfg: Config, idx, doc: dict, modules: list[Path]) -> list[str]:
    """Why derived.json no longer matches the code, values and inputs; empty if it does."""
    reasons: list[str] = []
    recorded = sorted(doc.get("modules") or [])
    current = sorted(cfg.rel(p) for p in modules)
    if recorded != current:
        reasons.append("values modules changed (" + ", ".join(sorted(set(recorded) ^ set(current)))
                       + ")")
    for rel, h in sorted((doc.get("code") or {}).items()):
        if file_digest(cfg.root / rel) != h:
            reasons.append(f"{rel} changed")
    for key, d in sorted((doc.get("definitions") or {}).items()):
        for dep, h in sorted((d.get("deps") or {}).items()):
            cur = entry_hash(idx, dep)
            if cur != h:
                reasons.append(f"{dep} " + ("no longer exists" if cur is None else "changed")
                               + f" (read by {key})")
        for path, h in sorted((d.get("inputs") or {}).items()):
            if hash_path(cfg.root / path) != h:
                reasons.append(f"input {path} changed (read by {key})")
    return list(dict.fromkeys(reasons))


@dataclasses.dataclass
class Outcome:
    issues: list[Issue]
    evaluated: bool = False
    written: Path | None = None
    removed: Path | None = None
    modules: list[str] = dataclasses.field(default_factory=list)


def prepare(cfg: Config, idx, *, build: bool) -> Outcome:
    """Bring the index up to date with the derived values.

    For ``check`` (``build=False``) the recorded results are loaded, and anything that
    moved since they were computed is ``out-of-sync``. For ``build``, the definitions
    are re-evaluated when something moved (or never were), and derived.json rewritten.
    """
    modules = values_modules(cfg)
    doc = load(cfg)
    path = path_of(cfg)
    issues: list[Issue] = []
    if doc is not None and (doc.get("schema") != SCHEMA or not verify_record(doc)):
        if not build:
            issues.append(Issue("store-edited", "error",
                                f"{cfg.rel(path)} does not match its record_hash: it was edited by "
                                f"hand. Never edit .vouch/ directly.", cfg.rel(path),
                                subject=cfg.rel(path), fix="vouch build", fix_kind="build"))
            return Outcome(issues)
        doc = None

    if not build:
        if doc is None or (modules and stale_probe(cfg, idx, doc, modules)):
            idx.unevaluated = static_keys(modules)
        if doc is None:
            if modules:
                issues.append(Issue("out-of-sync", "error",
                                    f"{', '.join(cfg.rel(m) for m in modules)} has not been "
                                    f"evaluated (run `vouch build`)", subject=cfg.rel(path),
                                    fix="vouch build", fix_kind="build"))
            return Outcome(issues)
        idx.add_derived(doc)
        reasons = stale_reasons(cfg, idx, doc, modules)
        if reasons:
            more = f" (+{len(reasons) - 4} more)" if len(reasons) > 4 else ""
            issues.append(Issue("out-of-sync", "error",
                                "derived values are out of date: " + "; ".join(reasons[:4]) + more
                                + " (run `vouch build`)", subject=cfg.rel(path),
                                fix="vouch build", fix_kind="build",
                                detail={"reasons": reasons}))
        return Outcome(issues + _issues(doc))

    if not modules:
        if path.exists():
            path.unlink()
            return Outcome(issues, removed=path)
        return Outcome(issues)
    if doc is not None:
        probe = idx.clone()
        probe.add_derived(doc)
        if not stale_reasons(cfg, probe, doc, modules):
            idx.add_derived(doc)
            return Outcome(issues + _issues(doc))
    new, found = evaluate(cfg, idx, modules)
    idx.derived_doc = new
    text = dumps(new)
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        old = None
    written = None
    if old != text:
        atomic_write_text(path, text)
        written = path
    return Outcome(issues + found, evaluated=True, written=written,
                   modules=[cfg.rel(m) for m in modules])
