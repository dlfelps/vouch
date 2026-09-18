"""``vouch build``: render every value, write the generated LaTeX and the provenance CSV.

``plan()`` computes what the files *should* contain without touching disk, so
``vouch check`` can later report ``out-of-sync`` by comparing; ``build()`` writes.
A file is rewritten only when its content changes, so an unchanged build never
makes latexmk recompile.
"""

from __future__ import annotations

import dataclasses
import math
import os
from pathlib import Path
from typing import Any

from .config import Config
from .index import Entry, Index, Table
from .render import Options, RenderError, Rendered, render
from .tex import emit
from .tex.scan import Citation, Document, scan
from .values import Stat

VALUE_KINDS = ("value", "stat-field", "param", "table-cell")


@dataclasses.dataclass
class Issue:
    check: str
    severity: str              # error | warning | info
    message: str
    file: str | None = None
    line: int | None = None
    fix: str | None = None

    def where(self) -> str:
        return f"{self.file}:{self.line}" if self.file and self.line else (self.file or "")


@dataclasses.dataclass
class PaperPlan:
    main: Path
    values_path: Path
    tables_dir: Path
    csv_path: Path
    doc: Document
    files: dict[Path, str]                       # path -> content
    rendered: dict[tuple[str, str], Rendered]
    issues: list[Issue]
    counts: dict[str, int]


@dataclasses.dataclass
class BuildResult:
    plans: list[PaperPlan]
    issues: list[Issue]                          # project-level (store problems)
    written: list[Path]
    unchanged: list[Path]


class BuildError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# tooltips
# ---------------------------------------------------------------------------

def _num(x: Any) -> str:
    if isinstance(x, bool):
        return str(x).lower()
    if isinstance(x, float):
        return f"{x:.10g}"
    if isinstance(x, Stat):
        return f"{x.mean:.10g} +/- {x.std:.10g} (n={x.n})"
    if isinstance(x, tuple):
        return "(" + ", ".join(_num(v) for v in x) + ")"
    return str(x)


def _when(started: str | None) -> str:
    if not started:
        return ""
    return started.replace("T", " ")[:16] + " UTC"


def _provenance(idx: Index, e: Entry, with_site: bool = True) -> list[str]:
    rec = idx.runs.get(e.run or "") or {}
    first = f"run {e.run}" + (f" | {e.site}" if with_site and e.site else "")
    cmd = " ".join(rec.get("command") or [])
    git = rec.get("git") or {}
    stamp = " | ".join(p for p in (_when(rec.get("started")),
                                   f"git {git['commit'][:7]}" + (" (dirty)" if git.get("dirty") else "")
                                   if git.get("commit") else "") if p)
    return [first, cmd, stamp]


def value_tooltip(idx: Index, e: Entry) -> str:
    lines = [f"{e.key} = {_num(e.raw)}", e.desc or ""]
    if e.kind == "table-cell":
        lines.append(f"table {e.parent}")
    lines += _provenance(idx, e, with_site=e.kind != "param")
    return emit.tooltip(lines)


def claim_tooltip(idx: Index, e: Entry) -> str:
    vals = e.extra.get("values") or {}
    lines = [f"claim {e.key}: {'HOLDS' if e.raw else 'FALSE'}", e.desc or "",
             ", ".join(f"{k}={_num(v)}" for k, v in vals.items())]
    return emit.tooltip(lines + _provenance(idx, e))


def raw_text(x: Any) -> str:
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, Stat):
        return repr(float(x.mean))
    if isinstance(x, float):
        return repr(x) if math.isfinite(x) else str(x)
    if isinstance(x, tuple):
        return ",".join(raw_text(v) for v in x)
    if isinstance(x, str):
        return emit.tip_escape(x)
    return str(x)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def _rank_value(x: Any) -> float | None:
    if isinstance(x, Stat):
        return x.mean
    if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x)):
        return float(x)
    return None


def _table_rows(t: Table, idx: Index, rendered: dict, issues: list[Issue]) -> list[list[str]]:
    marks: dict[tuple[int, str], str] = {}
    for col, mode in t.highlight.items():
        if col not in t.columns:
            issues.append(Issue("table", "warning", f"table {t.key}: highlight column {col!r} "
                                                     f"does not exist"))
            continue
        ranked = []
        for i in range(len(t.rows)):
            key = t.cell_key(i, col)
            e = idx.get(key)
            v = _rank_value(e.raw) if e else None
            if v is not None:
                ranked.append((v, rendered[(key, "")].latex, i))
        if not ranked:
            continue
        direction = mode
        if mode == "best":
            better = idx.get(t.cell_key(ranked[0][2], col)).better
            direction = {"higher": "max", "lower": "min"}.get(better or "")
            if direction is None:
                issues.append(Issue("table", "warning", f"table {t.key}: highlight 'best' on "
                                    f"{col!r} needs better= (in record or [metrics])"))
                continue
        ranked.sort(key=lambda r: r[0], reverse=(direction == "max"))
        # ties are decided as printed: cells that read the same are equally best
        levels = list(dict.fromkeys(text for _, text, _ in ranked))
        for _, text, i in ranked:
            if text == levels[0]:
                marks[(i, col)] = "best"
            elif t.second and len(levels) > 1 and text == levels[1]:
                marks[(i, col)] = "second"
    rows = []
    for i in range(len(t.rows)):
        cells = []
        for col in t.columns:
            c = emit.cite(t.cell_key(i, col))
            mark = marks.get((i, col))
            cells.append(emit.best_cell(c) if mark == "best" else
                         emit.second_cell(c) if mark == "second" else c)
        rows.append(cells)
    return rows


# ---------------------------------------------------------------------------
# planning one paper
# ---------------------------------------------------------------------------

def paper_paths(cfg: Config, paper: dict) -> tuple[Path, Path, Path, Path]:
    main = (cfg.root / paper["main"]).resolve()
    d = main.parent

    def opt(name: str, default: Path) -> Path:
        v = paper.get(name)
        return (cfg.root / v).resolve() if v else default
    return (main, opt("values_file", d / "vouch-values.tex"),
            opt("tables_dir", d / "vouch-tables"), opt("provenance_csv", d / "vouch-provenance.csv"))


def plan_paper(cfg: Config, idx: Index, paper: dict) -> PaperPlan:
    from .provenance import csv_text

    main, values_path, tables_dir, csv_path = paper_paths(cfg, paper)
    if not main.is_file():
        raise BuildError(f"paper main file {cfg.rel(main)} does not exist (vouch.toml [[paper]])")
    opts = Options.from_config(cfg)
    doc = scan(main, cfg.root, cfg.get("lint", "skip_envs", ()))
    issues: list[Issue] = []
    for f, line, target in doc.missing_inputs:
        issues.append(Issue("config", "warning", f"\\input{{{target}}} not found", f, line))

    # what the paper cites, and in which formats
    wanted: dict[str, set[str]] = {}
    for c in doc.citations:
        if c.kind in ("value", "raw", "claim", "table"):
            e = idx.get(c.key)
            if e is None:
                sugg = idx.suggest(c.key)
                issues.append(Issue("unknown-key", "error",
                                    f"\\{'vouch' if c.kind == 'value' else 'vouch' + c.kind}"
                                    f"{{{c.key}}} is not recorded by any run"
                                    + (f" (did you mean {', '.join(sugg)}?)" if sugg else ""),
                                    c.file, c.line,
                                    fix="fix the key, or record it (vouch.record / record_all)"))
                continue
            if c.kind == "value" and c.fmt:
                wanted.setdefault(c.key, set()).add(c.fmt)
        elif c.kind == "figure" and not c.resolved:
            issues.append(Issue("figure-missing", "warning",
                                f"\\includegraphics{{{c.written}}}: file not found", c.file, c.line))

    # render: every value at its default format, plus each cited override
    rendered: dict[tuple[str, str], Rendered] = {}
    lines: list[str] = []
    n_values = 0
    for key in sorted(idx.entries):
        e = idx.entries[key]
        if e.kind not in VALUE_KINDS:
            continue
        tip = value_tooltip(idx, e)
        n_values += 1
        for fmt in [""] + sorted(wanted.get(key, ())):
            try:
                r = render(e.raw, fmt or e.fmt, unit=e.unit, opts=opts)
            except (RenderError, ValueError) as exc:
                cites = [c for c in doc.citations if c.key == key and (c.fmt or "") == fmt]
                where = cites[0] if cites else None
                issues.append(Issue("format", "error" if fmt else "warning",
                                    f"{key}: format {fmt or e.fmt!r} cannot render "
                                    f"{type(e.raw).__name__} ({exc})",
                                    where.file if where else None, where.line if where else None))
                if fmt:
                    continue
                r = render(e.raw, None, unit=e.unit, opts=opts)
            rendered[(key, fmt)] = r
            lines.append(emit.set_line(key, fmt, r.latex, r.plain, tip, False))
        lines.append(emit.raw_line(key, raw_text(e.raw)))

    n_claims = 0
    for key in sorted(idx.entries):
        e = idx.entries[key]
        if e.kind == "claim":
            n_claims += 1
            lines.append(emit.claim_line(key, bool(e.raw), claim_tooltip(idx, e), False))

    files: dict[Path, str] = {}
    for key in sorted(idx.tables):
        t = idx.tables[key]
        path = tables_dir / f"{key}.tex"
        rel_for_tex = os.path.relpath(path, main.parent).replace(os.sep, "/")
        lines.append(emit.table_line(key, rel_for_tex))
        header = (f"GENERATED by `vouch build` from run {t.run}"
                  + (f" ({t.site})" if t.site else "") + ". Do not edit.")
        files[path] = emit.table_body(header, _table_rows(t, idx, rendered, issues), t.midrules)

    summary = f"{n_values} values | {n_claims} claims | {len(idx.tables)} tables"
    files[values_path] = emit.values_file(lines, summary)
    files[csv_path] = csv_text(doc, idx, rendered, cfg)
    counts = {"values": n_values, "claims": n_claims, "tables": len(idx.tables),
              "citations": len(doc.citations)}
    return PaperPlan(main, values_path, tables_dir, csv_path, doc, files, rendered, issues, counts)


def papers(cfg: Config) -> list[dict]:
    got = cfg.data.get("paper") or []
    if isinstance(got, dict):
        got = [got]
    if not got:
        raise BuildError("no [[paper]] in vouch.toml; add one, e.g.\n\n"
                         "  [[paper]]\n  main = \"paper/main.tex\"\n\nor run `vouch init`")
    for p in got:
        if "main" not in p:
            raise BuildError("every [[paper]] in vouch.toml needs main = \"path/to/main.tex\"")
    return got


def plan(cfg: Config) -> tuple[Index, list[PaperPlan], list[Issue]]:
    idx = Index.load(cfg)
    project = [Issue(p.check, "error" if p.check in ("store-edited", "key-conflict") else "warning",
                     p.message) for p in idx.problems]
    return idx, [plan_paper(cfg, idx, p) for p in papers(cfg)], project


def build(cfg: Config) -> BuildResult:
    _, plans, project = plan(cfg)
    written, unchanged = [], []
    for pl in plans:
        for path, content in pl.files.items():
            try:
                old = path.read_text(encoding="utf-8")
            except OSError:
                old = None
            if old == content:
                unchanged.append(path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            written.append(path)
    return BuildResult(plans, project, written, unchanged)
