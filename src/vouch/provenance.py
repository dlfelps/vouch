"""The provenance CSV: one row per key the paper cites, in reading order (SPEC §10)."""

from __future__ import annotations

import csv
import io
import json
import shlex
from typing import Any

from .config import Config
from .hashing import short
from .index import Entry, Index
from .render import Rendered
from .tex.scan import Document
from .values import Stat

COLUMNS = ("key", "kind", "rendered", "raw_value", "fmt", "unit", "description", "better",
           "experiment", "script", "call_site", "command", "params", "inputs", "git_commit",
           "git_dirty", "recorded_at", "duration_s", "freshness", "change_status",
           "previous_value", "acked_at", "cited_at")

KIND = {"value": "value", "stat-field": "value", "param": "param", "claim": "claim",
        "table": "table", "table-cell": "table-cell"}


def _raw_json(x: Any) -> str:
    if isinstance(x, Stat):
        d = {"mean": x.mean, "std": x.std, "n": x.n}
        return json.dumps(d)
    if isinstance(x, tuple):
        return json.dumps(list(x))
    if isinstance(x, float) and x != x:
        return "NaN"
    return json.dumps(x)


def _run_columns(idx: Index, run: str | None) -> dict[str, str]:
    rec = idx.runs.get(run or "") or {}
    if not rec:
        return {}
    git = rec.get("git") or {}
    cmd = rec.get("command") or []
    return {
        "experiment": run or "",
        "script": rec.get("entry") or "",
        "command": " ".join(shlex.quote(a) for a in cmd),
        "params": json.dumps(rec.get("params") or {}, sort_keys=True),
        "inputs": "; ".join(f"{p}@{short(h)}" for p, h in sorted((rec.get("inputs") or {}).items())),
        "git_commit": git.get("commit", ""),
        "git_dirty": "" if not git else str(bool(git.get("dirty"))).lower(),
        "recorded_at": rec.get("started", ""),
        "duration_s": str(rec.get("duration_s", "")),
    }


def rows(doc: Document, idx: Index, rendered: dict[tuple[str, str], Rendered],
         include_uncited: bool = False) -> list[dict[str, str]]:
    order: list[str] = []
    cited_at: dict[str, list[str]] = {}
    fmts: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for c in doc.citations:
        k = c.key
        if k not in cited_at:
            order.append(k)
            cited_at[k] = []
            fmts[k] = []
            kinds[k] = c.kind
        loc = f"{c.file}:{c.line}"
        if loc not in cited_at[k]:
            cited_at[k].append(loc)
        f = c.fmt or ""
        if c.kind == "value" and f not in fmts[k]:
            fmts[k].append(f)

    out: list[dict[str, str]] = []

    def value_row(key: str, e: Entry, fmt_list: list[str], where: list[str]) -> dict[str, str]:
        row = dict.fromkeys(COLUMNS, "")
        row.update(key=key, kind=KIND.get(e.kind, e.kind), unit=e.unit or "",
                   description=e.desc or "", better=e.better or "", call_site=e.site or "",
                   cited_at="; ".join(where))
        if e.kind == "claim":
            vals = e.extra.get("values") or {}
            row["raw_value"] = ("true" if e.raw else "false") + (
                " " + json.dumps(vals, sort_keys=True) if vals else "")
            row["rendered"] = "HOLDS" if e.raw else "FALSE"
        elif e.kind != "table":
            row["raw_value"] = _raw_json(e.raw)
            fl = fmt_list or [""]
            row["rendered"] = " | ".join(rendered[(key, f)].latex for f in fl if (key, f) in rendered)
            row["fmt"] = " | ".join(f or (e.fmt or "") for f in fl)
        row.update(_run_columns(idx, e.run))
        return row

    for key in order:
        kind = kinds[key]
        if kind == "figure":
            row = dict.fromkeys(COLUMNS, "")
            fig = idx.figures.get(key)
            row.update(key=key, kind="figure", cited_at="; ".join(cited_at[key]))
            if fig:
                row["call_site"] = fig.site or ""
                row.update(_run_columns(idx, fig.run))
            else:
                row["freshness"] = "untracked"
            out.append(row)
            continue
        e = idx.get(key)
        if e is None:
            row = dict.fromkeys(COLUMNS, "")
            row.update(key=key, kind="unknown", cited_at="; ".join(cited_at[key]))
            out.append(row)
            continue
        out.append(value_row(key, e, fmts.get(key, []), cited_at[key]))
        if e.kind == "table" and key in idx.tables:
            t = idx.tables[key]
            for i in range(len(t.rows)):
                for col in t.columns:
                    ck = t.cell_key(i, col)
                    ce = idx.get(ck)
                    if ce is not None:
                        out.append(value_row(ck, ce, [""], cited_at[key]))

    if include_uncited:
        seen = {r["key"] for r in out}
        for key in sorted(idx.entries):
            if key not in seen:
                out.append(value_row(key, idx.entries[key], [""], []))
    return out


def csv_text(doc: Document, idx: Index, rendered: dict, cfg: Config | None = None,
             include_uncited: bool = False) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows(doc, idx, rendered, include_uncited):
        w.writerow(r)
    return buf.getvalue()
