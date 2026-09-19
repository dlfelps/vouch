"""Records made from outside the experiment: ``vouch run`` and ``vouch import`` (SPEC §14).

``vouch run ID [--dep P]... [--input P]... [--out P]... -- CMD...`` records a run of
any program. The code it depends on is *declared* (``--dep``: Python files are
hashed semantically, anything else by content); the program hands its values back
through the JSON file at ``$VOUCH_VALUES`` or a results file it already writes
(``--values``). A Python command runs under ``python -m vouch.exec``, so the script
is tracked by function with no changes and its own ``vouch.record()`` calls land in
the same run.

``vouch import FILE --run ID`` registers a results file that already exists, with
the provenance that can honestly be claimed: declared producer files, the results
file itself as an input, and the command as stated -- all marked *imported*.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any

from .config import Config
from .hashing import hash_path
from .store import read_record, record_path, seal, write_record
from .units import analyze, raw_hash_text, read_source
from .values import Stat

_PKG_PARENT = str(Path(__file__).resolve().parent.parent)
_PYTHONS = {"python", "python3", "python.exe", "python3.exe", "py", "py.exe", "pythonw.exe"}


class RunError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# declared code: --dep / --producer
# ---------------------------------------------------------------------------

def _files_under(cfg: Config, path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    exclude = set(cfg.get("python", "exclude", []) or [])
    out = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude and not d.startswith("."))
        out += [Path(dirpath) / f for f in sorted(filenames) if not f.startswith(".")]
    return out


def declared_code(cfg: Config, paths: list[str], granularity: str) -> tuple[dict, dict]:
    """(the record's ``code``, extra ``inputs``) for declared dependencies: every unit of
    each Python file, and the content hash of every other file."""
    units: dict[str, str] = {}
    files: dict[str, str] = {}
    inputs: dict[str, str | None] = {}
    for p in paths:
        path = Path(p).resolve()
        if not path.exists():
            raise RunError(f"{p} does not exist")
        for f in _files_under(cfg, path):
            rel = cfg.rel(f)
            if f.suffix == ".py":
                try:
                    src = read_source(f)
                except (OSError, SyntaxError, UnicodeDecodeError):
                    src = None
                got = analyze(src, rel) if src is not None else None
                if got is None:
                    units[f"{rel}::<module>"] = "unparseable"
                    continue
                files[rel] = raw_hash_text(src)
                for qual, h in got[0].items():
                    units[f"{rel}::{qual}"] = h
            else:
                inputs[rel] = hash_path(f)
    code = {"granularity": granularity, "units": dict(sorted(units.items())),
            "files": dict(sorted(files.items()))}
    return code, inputs


def _env() -> dict:
    return {"python": None, "platform": sysconfig.get_platform(), "packages": {}}


# ---------------------------------------------------------------------------
# values handed back by the program
# ---------------------------------------------------------------------------

_FULL = {"values", "claims", "artifacts", "tables", "params"}


def _value_of(v: Any) -> Any:
    if isinstance(v, dict) and {"mean", "std", "n"} <= set(v):
        return Stat(float(v["mean"]), float(v["std"]), int(v["n"]),
                    min=v.get("min"), max=v.get("max"))
    if isinstance(v, list):
        return tuple(v)
    return v


def apply_values(run, data: Any, site: str, *, prefix: str | None = None,
                 row_key: str | None = None, stats: bool = False) -> list[str]:
    """Record what a program handed back. Returns the artifact paths it declared.

    Full form: ``{"values": {key: {"value": ..., "fmt": ..., "desc": ...}}, "claims": {},
    "artifacts": [...], "tables": {}, "params": {}}``; anything else is recorded like
    ``record_all`` (``{"cifar.vit.acc": 0.912}``, nested dicts, rows).
    """
    from .api import _Notes, _warn
    if isinstance(data, dict) and data and set(data) <= _FULL and isinstance(data.get("values", {}), dict):
        notes = _Notes()
        for key, v in (data.get("values") or {}).items():
            meta = v if isinstance(v, dict) and "value" in v else {"value": v}
            full = run._key(key if not prefix else f"{prefix}.{key}")
            entry = run._entry(full, _value_of(meta["value"]), meta.get("fmt"), meta.get("unit"),
                               meta.get("desc"), meta.get("better"), site, notes)
            if entry is not None:
                run._put(full, entry)
        run._report_bulk(notes, list(data.get("values") or {}), who="vouch run")
        for key, c in (data.get("claims") or {}).items():
            c = c if isinstance(c, dict) else {"holds": c}
            run.claim(key, bool(c.get("holds")), desc=c.get("desc"), values=c.get("values"))
            run._claims[run._key(key)]["site"] = site
        for key, t in (data.get("tables") or {}).items():
            t = dict(t) if isinstance(t, dict) else {"rows": t}
            rows = t.pop("rows", [])
            run.table(key, rows, _site_override=site,
                      **{k: t[k] for k in ("columns", "row_key", "fmt", "highlight", "second",
                                           "midrules", "desc") if k in t})
        if data.get("params"):
            run.params(data["params"])
        arts = data.get("artifacts") or []
        return [str(a) for a in (arts if isinstance(arts, list) else [arts])]
    if data in (None, {}, []):
        return []
    run.record_all(data, prefix=prefix, row_key=row_key, stats=stats, _site_override=site)
    if not run._values and not run._tables:
        _warn("vouch run: the values file held nothing recordable")
    return []


# ---------------------------------------------------------------------------
# vouch run
# ---------------------------------------------------------------------------

def _python_command(cmd: list[str]) -> bool:
    exe = Path(cmd[0]).name.lower()
    return (exe in _PYTHONS or os.path.abspath(cmd[0]) == os.path.abspath(sys.executable)) \
        and len(cmd) > 1 and cmd[1].endswith(".py")


def run_command(cfg: Config, run_id: str, cmd: list[str], **kw) -> int:
    """Run ``cmd`` and record it as run ``run_id``. Returns the program's exit code."""
    from . import api
    prev = api._project_obj
    try:
        return _run_command(cfg, run_id, cmd, **kw)
    finally:
        api._project_obj = prev                   # the calling process records as before


def _run_command(cfg: Config, run_id: str, cmd: list[str], *, deps: list[str] = (),
                 inputs: list[str] = (), outs: list[str] = (), values: str | None = None,
                 prefix: str | None = None, row_key: str | None = None, stats: bool = False,
                 argv: list[str] | None = None, out=print) -> int:
    from . import api
    if not cmd:
        raise RunError("no command to run (vouch run ID ... -- CMD ...)")
    api.use_project(cfg)
    code, dep_inputs = declared_code(cfg, list(deps), "deps")
    run = api.Run(run_id)
    for p in inputs:
        run.input(p)
    is_py = _python_command(cmd)
    real = [cmd[0], "-m", "vouch.exec", *cmd[1:]] if is_py else list(cmd)
    target = record_path(cfg.store, run.id)
    before = target.read_bytes() if target.exists() else None

    fd, tmp = tempfile.mkstemp(prefix=f"vouch-values-{run.id}-", suffix=".json")
    os.close(fd)
    os.unlink(tmp)
    env = dict(os.environ, VOUCH_RUN=run.id, VOUCH_ROOT=str(cfg.root), VOUCH_VALUES=tmp)
    if is_py:
        env["PYTHONPATH"] = os.pathsep.join([_PKG_PARENT] + [p for p in
                                            env.get("PYTHONPATH", "").split(os.pathsep) if p])
    try:
        rc = subprocess.run(real, env=env).returncode
    except OSError as exc:
        raise RunError(f"could not start {cmd[0]}: {exc}") from None
    try:
        if rc != 0:
            after = target.read_bytes() if target.exists() else None
            if after != before:                    # the child finished its record anyway
                if before is None:
                    target.unlink()
                else:
                    target.write_bytes(before)
            out(f"vouch run: {cmd[0]} exited {rc}; run {run.id} not recorded "
                f"(its previous record, if any, is unchanged)")
            return rc

        site = "(vouch run)"
        data = None
        if values:
            from .api import _load_results_file
            vp = Path(values)
            run.input(vp)
            data = _load_results_file(vp)
            site = cfg.rel(vp)
        elif os.path.exists(tmp):
            with open(tmp, encoding="utf-8") as fh:
                data = json.load(fh)
            site = "$VOUCH_VALUES"
        declared = apply_values(run, data, site, prefix=prefix, row_key=row_key, stats=stats)
        for p in list(outs) + declared:
            run._add_artifact(p, None, "(vouch run --out)")

        command = ["vouch", "run", *(argv or [run.id, "--", *cmd])]
        child = None
        after = target.read_bytes() if target.exists() else None
        if is_py and after is not None and after != before:
            child = read_record(target)
        record = run._build_record(code=code, command=command,
                                   entry=cfg.rel(cmd[1]) if is_py else cmd[0], env=_env())
        record["inputs"] = dict(sorted({**record["inputs"], **dep_inputs}.items()))
        if child is not None:
            record = _merge(child, record)
        path = write_record(cfg.store, seal(record))
        n = len(record["values"])
        how = ("tracked by function (python -m vouch.exec)" if child is not None
               else f"declared deps ({len(code['files']) + len(dep_inputs)} files)")
        out(f"vouch run: recorded run {run.id}: {n} value(s), {len(record['artifacts'])} "
            f"artifact(s) -> {cfg.rel(path)} · code {how}")
        return 0
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _merge(child: dict, outer: dict) -> dict:
    """The Python child's record (function-level code, its own values), plus what the
    wrapper knows: declared deps, inputs, outputs, handed-back values, the command."""
    rec = dict(child)
    code = dict(child.get("code") or {})
    code["units"] = dict(sorted({**outer["code"]["units"], **(code.get("units") or {})}.items()))
    code["files"] = dict(sorted({**outer["code"]["files"], **(code.get("files") or {})}.items()))
    rec["code"] = code
    for section in ("inputs", "values", "claims", "artifacts", "tables", "params"):
        rec[section] = dict(sorted({**(child.get(section) or {}), **(outer.get(section) or {})}.items()))
    rec["command"] = outer["command"]
    rec["duration_s"] = outer["duration_s"]
    rec.pop("record_hash", None)
    return rec


# ---------------------------------------------------------------------------
# vouch import
# ---------------------------------------------------------------------------

def import_file(cfg: Config, path: str, run_id: str, **kw) -> dict:
    """Register an existing results file as run ``run_id``; returns the record."""
    from . import api
    prev = api._project_obj
    try:
        return _import_file(cfg, path, run_id, **kw)
    finally:
        api._project_obj = prev


def _import_file(cfg: Config, path: str, run_id: str, *, prefix: str | None = None,
                 producers: list[str] = (), command: str | None = None,
                 row_key: str | None = None, stats: bool = False) -> dict:
    from . import api
    p = Path(path)
    if not p.is_file():
        raise RunError(f"{path} does not exist")
    api.use_project(cfg)
    code, dep_inputs = declared_code(cfg, list(producers), "declared")
    run = api.Run(run_id)
    rel = cfg.rel(p)
    run.record_all(str(p), prefix=prefix, row_key=row_key, stats=stats, _site_override=rel)
    py = [f for f in code["files"]]
    record = run._build_record(code=code, command=shlex.split(command) if command else [],
                               entry=py[0] if py else None, env=_env())
    record["inputs"] = dict(sorted({**record["inputs"], **dep_inputs}.items()))
    record["imported"] = {"file": rel, "command": "declared" if command else None,
                          "producers": [cfg.rel(Path(x).resolve()) for x in producers]}
    write_record(cfg.store, record)
    return record
