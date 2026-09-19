"""Run records on disk: ``.vouch/runs/<run-id>.json``.

The file is the provenance record, committed to git, so two properties matter:

* **Deterministic, one entry per line.** Keys are sorted and every map a person
  would diff (values, units, artifacts, ...) is written one entry per line, so a
  value that moved is a one-line diff (asqc's ``snapshot.write`` lesson).
* **Self-checking.** ``record_hash`` is SHA-256 over the canonical JSON of the
  record without that field. It catches a hand- or agent-edited number; it is not
  tamper-proof against someone who recomputes it (SPEC §16.1).

Hashes are over parsed content, never raw bytes, so a CRLF checkout verifies.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator

SCHEMA = "vouch/1"

TOP_ORDER = ("schema", "run", "status", "entry", "command", "params", "started",
             "duration_s", "git", "env", "code", "inputs", "values", "claims",
             "artifacts", "tables", "record_hash")

# maps written one entry per line (a path of keys from the top of the record)
EXPANDED = {(), ("params",), ("code",), ("code", "units"), ("code", "files"),
            ("code", "whole_files"),
            ("inputs",), ("values",), ("claims",), ("artifacts",), ("tables",)}

# maps keyed by user data (value keys, paths, column names): always sorted.
# "*" stands for any single user key.
USER_MAPS = {("params",), ("code", "units"), ("code", "files"), ("code", "whole_files"),
             ("inputs",), ("values",),
             ("claims",), ("artifacts",), ("tables",), ("env", "packages"),
             ("claims", "*", "values"), ("tables", "*", "fmt"), ("tables", "*", "highlight")}

# every other map is record structure, written in this reading order
FIELD_ORDER = ("type", "value", "holds", "hash", "kind", "granularity", "why", "units",
               "files", "whole_files",
               "python", "platform", "packages", "commit", "dirty", "mean", "std", "n",
               "min", "max", "fmt", "unit", "better", "desc", "columns", "row_key", "rows",
               "highlight", "second", "midrules", "values", "call", "function", "args",
               "over", "calls", "results", "seconds", "not_in_key", "sites", "via", "site")

# maps written in the order they were recorded (a call's arguments, in signature order)
KEEP_ORDER = {("values", "*", "call", "args"), ("values", "*", "call", "over")}


class RecordError(ValueError):
    pass


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def compute_record_hash(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "record_hash"}
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def verify_record(record: dict) -> bool:
    return record.get("record_hash") == compute_record_hash(record)


def _shape(path: tuple) -> tuple:
    """``path`` with each key directly under a user map replaced by ``"*"``."""
    out: list = []
    for part in path:
        out.append("*" if tuple(out) in USER_MAPS else part)
    return tuple(out)


def _ordered_keys(path: tuple, mapping: dict) -> list:
    shape = _shape(path)
    if shape == ():
        known = [k for k in TOP_ORDER if k in mapping]
        return known + sorted(k for k in mapping if k not in TOP_ORDER)
    if shape in USER_MAPS:
        return sorted(mapping)
    if shape in KEEP_ORDER:
        return list(mapping)
    rank = {k: i for i, k in enumerate(FIELD_ORDER)}
    return sorted(mapping, key=lambda k: (rank.get(k, len(rank)), k))


def _arrange(value: Any, path: tuple) -> Any:
    """``value`` with every dict's keys in display order (json.dumps keeps insertion order)."""
    if isinstance(value, dict):
        return {k: _arrange(value[k], path + (k,)) for k in _ordered_keys(path, value)}
    if isinstance(value, list):
        return [_arrange(v, path + ("*",)) for v in value]
    return value


def _inline(value: Any, path: tuple) -> str:
    return json.dumps(_arrange(value, path), ensure_ascii=False, allow_nan=False,
                      separators=(", ", ": "))


def _write(value: Any, path: tuple, indent: int, out: list[str]) -> None:
    if isinstance(value, dict) and _shape(path) in EXPANDED and value:
        pad = "  " * (indent + 1)
        out.append("{\n")
        keys = _ordered_keys(path, value)
        for i, k in enumerate(keys):
            out.append(f"{pad}{json.dumps(k, ensure_ascii=False)}: ")
            _write(value[k], path + (k,), indent + 1, out)
            out.append(",\n" if i < len(keys) - 1 else "\n")
        out.append("  " * indent + "}")
    else:
        out.append(_inline(value, path))


def dumps_record(record: dict) -> str:
    out: list[str] = []
    _write(record, (), 0, out)
    out.append("\n")
    return "".join(out)


def seal(record: dict) -> dict:
    """Return ``record`` with a fresh ``record_hash``."""
    record = dict(record)
    record.pop("record_hash", None)
    record["record_hash"] = compute_record_hash(record)
    return record


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def runs_dir(store: Path) -> Path:
    return Path(store) / "runs"


def record_path(store: Path, run_id: str) -> Path:
    return runs_dir(store) / f"{run_id}.json"


def ensure_store(store: Path) -> None:
    """Create ``.vouch/`` with its own .gitignore, so ``cache/`` is never committed."""
    store = Path(store)
    runs_dir(store).mkdir(parents=True, exist_ok=True)
    gi = store / ".gitignore"
    if not gi.exists():
        gi.write_text("cache/\n", encoding="utf-8", newline="\n")


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_record(store: Path, record: dict) -> Path:
    record = seal(record)
    ensure_store(store)
    path = record_path(store, record["run"])
    atomic_write_text(path, dumps_record(record))
    return path


def read_record(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise RecordError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RecordError(f"{path}: not a {SCHEMA} run record")
    return data


def iter_records(store: Path) -> Iterator[tuple[Path, dict]]:
    d = runs_dir(store)
    if not d.is_dir():
        return
    for path in sorted(d.glob("*.json")):
        yield path, read_record(path)


def load_runs(store: Path) -> dict[str, dict]:
    return {rec["run"]: rec for _, rec in iter_records(store)}
