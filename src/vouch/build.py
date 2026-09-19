"""``vouch build``: render every value, write the generated LaTeX and the provenance CSV.

``plan()`` computes everything -- freshness, pending changes, the content every
generated file *should* have -- without touching disk, so ``vouch check`` reuses
it read-only and reports ``out-of-sync`` by comparing. ``build()`` writes, and
acknowledges the changes that cannot have made prose wrong. A file is rewritten
only when its content changes, so an unchanged build never makes latexmk recompile.

Run freshness depends on the working tree, not on what was recorded, so it is kept
out of the content that ``out-of-sync`` compares: it lives on ``\\vouch@state``
lines of the values file (which tooltips read) and in the CSV's freshness column.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import math
import os
from pathlib import Path
from typing import Any

from . import changes as ch
from .config import Config
from .freshness import RunState, assess
from .index import Entry, Index, Table, source_runs
from .issues import Issue
from .render import Options, RenderError, Rendered, render
from .tex import emit
from .tex.scan import Document, scan
from .values import Stat

VALUE_KINDS = ("value", "stat-field", "element", "param", "table-cell")


@dataclasses.dataclass
class PaperPlan:
    main: Path
    values_path: Path
    tables_dir: Path
    csv_path: Path | None
    doc: Document
    rendered: dict[tuple[str, str], Rendered]
    wanted: dict[str, set[str]]
    issues: list[Issue]
    files: dict[Path, str] = dataclasses.field(default_factory=dict)
    counts: dict[str, int] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Context:
    cfg: Config
    idx: Index
    states: dict[str, RunState]
    baseline: dict[str, dict]
    plans: list[PaperPlan]
    changes: list[ch.Change]
    project_issues: list[Issue]
    derived: object = None               # derive.Outcome: evaluated, written, removed

    @property
    def pending(self) -> dict[str, ch.Change]:
        return {c.key: c for c in self.changes if c.pending}


@dataclasses.dataclass
class BuildResult:
    ctx: Context
    written: list[Path]
    unchanged: list[Path]
    auto_acked: list[ch.Change]
    notified: list[ch.Change]
    notify_error: str | None

    @property
    def plans(self) -> list[PaperPlan]:
        return self.ctx.plans

    @property
    def issues(self) -> list[Issue]:
        return self.ctx.project_issues


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


def _derived_lines(e: Entry) -> list[str]:
    """Where a derived value comes from, one plain line per fact."""
    d = e.extra.get("derived")
    if not d:
        return []
    deps = [k for k in d.get("deps") or [] if not k.startswith("keys:")]
    lines = [f"derived by {d.get('function', '?')}" + (f" at {d['site']}" if d.get("site") else "")]
    if deps:
        lines.append("from " + ", ".join(deps[:6]) + (f" (+{len(deps) - 6} more)" if len(deps) > 6 else ""))
    if d.get("inputs"):
        lines.append("reads " + ", ".join(sorted(d["inputs"])))
    if d.get("runs"):
        lines.append("runs " + ", ".join(d["runs"]))
    return lines


def _alias_line(e: Entry) -> str:
    return f"alias of {e.extra['alias_of']}" if e.extra.get("alias_of") else ""


def _provenance(idx: Index, e: Entry, with_site: bool = True) -> list[str]:
    if e.extra.get("derived"):
        return _derived_lines(e)
    rec = idx.runs.get(e.run or "") or {}
    first = f"run {e.run}" + (" (imported)" if rec.get("imported") else "") \
        + (f" | {e.site}" if with_site and e.site else "")
    cmd = " ".join(rec.get("command") or [])
    git = rec.get("git") or {}
    stamp = " | ".join(p for p in (_when(rec.get("started")),
                                   f"git {git['commit'][:7]}" + (" (dirty)" if git.get("dirty") else "")
                                   if git.get("commit") else "") if p)
    return [first, cmd, stamp]


def _with_state(tip: str, runs: list[str], change: ch.Change | None) -> str:
    parts = [tip]
    for run in runs:
        parts.append(r"\vouch@runstate{" + run + "}")
    if change is not None:
        parts.append(emit.tip_escape(ch.was_text(change)))
    return emit.TIP_NEWLINE.join(parts)


def _call_line(e: Entry) -> str:
    from .track import call_text
    call = e.extra.get("call")
    return f"recorded by {call_text(call)}" if call else ""


def _call_lines(e: Entry) -> list[str]:
    """What @vouch.track / [[track]] knows about the call, one plain line per fact."""
    from .track import per_call_text, sites_text, timing_text
    call = e.extra.get("call")
    if not call:
        return []
    lines = [_call_line(e)]
    if call.get("seconds"):
        lines.append(timing_text(call, ascii=True))
    each = per_call_text(call)
    if each:
        lines.append(f"each call: {each}")
    where = f"function {call.get('function', '?')}" + (f" at {e.site}" if e.site else "")
    if call.get("sites"):
        where += f", called at {sites_text(call)}"
    if call.get("via"):
        where += f"; listed in vouch.toml {call['via']}"
    lines.append(where)
    return lines


def value_tooltip(idx: Index, e: Entry, change: ch.Change | None = None) -> str:
    lines = [f"{e.key} = {_num(e.raw)}", e.desc or "", _alias_line(e)] + _call_lines(e)
    if e.kind == "table-cell":
        lines.append(f"table {e.parent}")
    lines += _provenance(idx, e, with_site=e.kind != "param" and not e.extra.get("call"))
    return _with_state(emit.tooltip(lines), source_runs(e), change)


def claim_tooltip(idx: Index, e: Entry, change: ch.Change | None = None) -> str:
    lines = [f"claim {e.key}: {'HOLDS' if e.raw else 'FALSE'}", e.desc or "", _alias_line(e),
             _claim_detail(e)]
    return _with_state(emit.tooltip(lines + _provenance(idx, e)), source_runs(e), change)


def _claim_detail(e: Entry) -> str:
    """``0.932 > 0.912 (margin 2.2%)``, or the values the claim was about."""
    expl, margin = e.extra.get("explanation"), e.extra.get("margin")
    if expl:
        return expl + (f" (margin {margin:.1%})" if isinstance(margin, (int, float)) else "")
    vals = e.extra.get("values") or {}
    return ", ".join(f"{k}={_num(v)}" for k, v in vals.items())


def prov_latex(idx: Index, e: Entry, change: ch.Change | None = None) -> str:
    """The appendix entry for one key: typeset LaTeX, every field escaped."""
    from .render import tex_escape as esc
    rec = idx.runs.get(e.run or "") or {}
    first = []
    if e.desc:
        first.append(esc(e.desc))
    if e.kind == "claim":
        detail = _claim_detail(e)
        if detail:
            first.append(esc(detail))
    else:
        first.append("raw " + esc(_num(e.raw)))
    if e.kind == "table-cell":
        first.append(r"table \texttt{" + esc(e.parent or "") + "}")
    lines = [r"\quad ".join(first)]
    if e.extra.get("alias_of"):
        lines.append(r"alias of \texttt{" + esc(e.extra["alias_of"]) + "}")
    if e.extra.get("derived"):
        return _derived_latex(e, lines, change)
    call = e.extra.get("call")
    if call:
        lines += _call_latex(e, call)
    where = [r"run \texttt{" + esc(e.run or "?") + "}"]
    if rec.get("imported"):
        where.append("imported from " + r"\texttt{" + esc(str(rec["imported"].get("file"))) + "}")
    if e.site and e.kind != "param" and not call:          # a tracked value's site is above
        where.append(r"\texttt{" + esc(e.site) + "}")
    cmd = " ".join(rec.get("command") or [])
    if cmd:
        where.append(r"\texttt{" + esc(cmd) + "}")
    lines.append(r"\quad ".join(where))
    git = rec.get("git") or {}
    stamp = [p for p in (esc(_when(rec.get("started"))),
                         ("git " + esc(git["commit"][:7]) + (" (dirty)" if git.get("dirty") else ""))
                         if git.get("commit") else "") if p]
    if e.run:
        stamp.append(r"\vouch@runstate{" + e.run + "}")
    lines.append(r"\quad ".join(stamp))
    if change is not None:
        lines.append(r"\textcolor{vouchchanged}{" + esc(ch.was_text(change)) + "}")
    return r"{\footnotesize " + r"\newline ".join(ln for ln in lines if ln) + "}"


def _derived_latex(e: Entry, lines: list[str], change: ch.Change | None) -> str:
    """The appendix entry of a derived value: the definition, what it read, its runs."""
    from .render import tex_escape as esc
    d = e.extra["derived"]
    fn = r"derived by \texttt{" + esc(str(d.get("function", "?"))) + "}"
    if d.get("site"):
        fn += r" at \texttt{" + esc(d["site"]) + "}"
    lines.append(fn)
    deps = [k for k in d.get("deps") or [] if not k.startswith("keys:")]
    if deps:
        shown = ", ".join(r"\texttt{" + esc(k) + "}" for k in deps[:8])
        lines.append("from " + shown + (f" (+{len(deps) - 8} more)" if len(deps) > 8 else ""))
    if d.get("inputs"):
        lines.append("reads " + ", ".join(r"\texttt{" + esc(p) + "}" for p in sorted(d["inputs"])))
    runs = d.get("runs") or []
    if runs:
        lines.append("runs " + ", ".join(r"\texttt{" + esc(r) + r"} (\vouch@runstate{" + r + "})"
                                         for r in runs))
    if change is not None:
        lines.append(r"\textcolor{vouchchanged}{" + esc(ch.was_text(change)) + "}")
    return r"{\footnotesize " + r"\newline ".join(ln for ln in lines if ln) + "}"


def _call_latex(e: Entry, call: dict) -> list[str]:
    """The appendix lines for a tracked value: the call, each call's result, the code."""
    from .render import tex_escape as esc
    from .track import per_call_text, sites_text, timing_text
    lines = [esc(_call_line(e))]
    if call.get("seconds"):
        lines.append(esc(timing_text(call, ascii=True)))
    each = per_call_text(call)
    if each:
        lines.append("each call: " + esc(each))
    fn = r"function \texttt{" + esc(str(call.get("function", "?"))) + "}"
    if e.site:
        fn += r" at \texttt{" + esc(e.site) + "}"
    if call.get("sites"):
        fn += r", called at \texttt{" + esc(sites_text(call)) + "}"
    if call.get("via"):
        fn += r"; listed in \texttt{vouch.toml} " + esc(str(call["via"]))
    lines.append(fn)
    return lines


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


def state_text(st: RunState) -> str:
    word = {"fresh": "fresh", "cosmetic": "fresh", "accepted": "stale, accepted"}.get(st.state)
    if word:
        return emit.tip_escape(f"state: {word}")
    return emit.tip_escape(f"STATE: {st.state.upper()} - {st.summary()}")


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
            if v is not None and (key, "") in rendered:
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
# phase 1: scan and render one paper
# ---------------------------------------------------------------------------

def paper_paths(cfg: Config, paper: dict) -> tuple[Path, Path, Path, Path | None]:
    """main, values file, tables dir, and the provenance CSV -- written by every build
    only when ``provenance_csv`` is set; otherwise ``vouch export --csv`` makes it on demand
    (the PDF's provenance appendix is the everyday view)."""
    main = (cfg.root / paper["main"]).resolve()
    d = main.parent

    def opt(name: str, default: Path | None) -> Path | None:
        v = paper.get(name)
        return (cfg.root / v).resolve() if v else default
    return (main, opt("values_file", d / "vouch-values.tex"),
            opt("tables_dir", d / "vouch-tables"), opt("provenance_csv", None))


def prepare_paper(cfg: Config, idx: Index, paper: dict) -> PaperPlan:
    main, values_path, tables_dir, csv_path = paper_paths(cfg, paper)
    if not main.is_file():
        raise BuildError(f"paper main file {cfg.rel(main)} does not exist (vouch.toml [[paper]])")
    opts = Options.from_config(cfg)
    doc = scan(main, cfg.root, cfg.get("lint", "skip_envs", ()))
    issues: list[Issue] = []
    for f, line, target in doc.missing_inputs:
        issues.append(Issue("config", "warning", f"\\input{{{target}}} not found", f, line))

    wanted: dict[str, set[str]] = {}
    for c in doc.citations:
        if c.kind in ("value", "raw", "claim", "table"):
            e = idx.get(c.key)
            if e is None and idx.awaiting_build(c.key):
                continue                 # reported once, as out-of-sync: run vouch build
            if e is None:
                sugg = idx.suggest(c.key)
                macro = {"value": "vouch", "raw": "vouchraw", "claim": "vouchclaim",
                         "table": "vouchtable"}[c.kind]
                issues.append(Issue("unknown-key", "error",
                                    f"\\{macro}{{{c.key}}} is not recorded by any run"
                                    + (f" (did you mean {', '.join(sugg)}?)" if sugg else ""),
                                    c.file, c.line, subject=c.key,
                                    fix="fix the key, or record it (vouch.record / record_all)",
                                    fix_kind="edit"))
                continue
            if c.kind == "value" and c.fmt:
                wanted.setdefault(c.key, set()).add(c.fmt)
        elif c.kind == "figure" and not c.resolved:
            issues.append(Issue("figure-missing", "warning",
                                f"\\includegraphics{{{c.written}}}: file not found", c.file, c.line))

    from .tex.lint import lint
    issues += lint(doc, idx, cfg)

    rendered: dict[tuple[str, str], Rendered] = {}
    for key in sorted(idx.entries):
        e = idx.entries[key]
        if e.kind not in VALUE_KINDS:
            continue
        for fmt in [""] + sorted(wanted.get(key, ())):
            try:
                rendered[(key, fmt)] = render(e.raw, fmt or e.fmt, unit=e.unit, opts=opts)
            except (RenderError, ValueError) as exc:
                cites = [c for c in doc.citations if c.key == key and (c.fmt or "") == fmt]
                where = cites[0] if cites else None
                if fmt or cites:
                    issues.append(Issue("format", "error" if fmt else "warning",
                                        f"{key}: format {fmt or e.fmt!r} cannot render "
                                        f"{type(e.raw).__name__} ({exc})",
                                        where.file if where else None,
                                        where.line if where else None, subject=key))
                if not fmt:
                    rendered[(key, "")] = render(e.raw, None, unit=e.unit, opts=opts)
    return PaperPlan(main, values_path, tables_dir, csv_path, doc, rendered, wanted, issues)


# ---------------------------------------------------------------------------
# phase 2: emit its files, knowing freshness and pending changes
# ---------------------------------------------------------------------------

def emit_paper(ctx: Context, pl: PaperPlan) -> None:
    from .provenance import csv_text

    idx, pending = ctx.idx, ctx.pending
    lines: list[str] = []
    n_values = n_claims = 0
    for key in sorted(idx.entries):
        e = idx.entries[key]
        if e.kind not in VALUE_KINDS:
            continue
        change = pending.get(key)
        tip = value_tooltip(idx, e, change)
        n_values += 1
        for fmt in [""] + sorted(pl.wanted.get(key, ())):
            r = pl.rendered.get((key, fmt))
            if r is not None:
                lines.append(emit.set_line(key, fmt, r.latex, r.plain, tip, change is not None))
        lines.append(emit.raw_line(key, raw_text(e.raw)))
        lines.append(emit.prov_line(key, prov_latex(idx, e, change)))

    for key in sorted(idx.entries):
        e = idx.entries[key]
        if e.kind == "claim":
            n_claims += 1
            change = pending.get(key)
            lines.append(emit.claim_line(key, bool(e.raw), claim_tooltip(idx, e, change),
                                         change is not None))
            lines.append(emit.prov_line(key, prov_latex(idx, e, change)))

    files: dict[Path, str] = {}
    for key in sorted(idx.tables):
        t = idx.tables[key]
        path = pl.tables_dir / f"{key}.tex"
        rel_for_tex = os.path.relpath(path, pl.main.parent).replace(os.sep, "/")
        lines.append(emit.table_line(key, rel_for_tex))
        header = (f"GENERATED by `vouch build` from {t.origin}"
                  + (f" ({t.site})" if t.site else "") + ". Do not edit.")
        files[path] = emit.table_body(header, _table_rows(t, idx, pl.rendered, pl.issues), t.midrules)

    for run in sorted(ctx.states):
        lines.append(emit.state_line(run, state_text(ctx.states[run])))

    summary = f"{n_values} values | {n_claims} claims | {len(idx.tables)} tables"
    files[pl.values_path] = emit.values_file(lines, summary)
    if pl.csv_path is not None:
        files[pl.csv_path] = csv_text(pl.doc, idx, pl.rendered, ctx)
    pl.files = files
    pl.counts = {"values": n_values, "claims": n_claims, "tables": len(idx.tables),
                 "citations": len(pl.doc.citations)}


# ---------------------------------------------------------------------------
# the whole project
# ---------------------------------------------------------------------------

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


def plan(cfg: Config, *, check_env: bool = True, only: list[dict] | None = None,
         evaluate: bool = False, need_paper: bool = True) -> Context:
    """Everything a build would write, and what is wrong. ``evaluate`` re-runs the
    definitions in vouch_values.py when they are out of date (``vouch build``);
    otherwise their last results are used, and staleness is reported."""
    from .derive import prepare
    idx = Index.load(cfg)
    outcome = prepare(cfg, idx, build=evaluate)
    project = [Issue(p.check, "error" if p.check in ("store-edited", "key-conflict") else "warning",
                     p.message, subject=p.subject,
                     fix="re-run the experiment; never edit .vouch/ by hand"
                     if p.check == "store-edited" else None)
               for p in idx.problems + idx.conflicts()]
    project += outcome.issues
    states = assess(cfg, idx.runs, check_env=check_env)
    if only is not None:
        chosen = only
    elif need_paper or cfg.data.get("paper"):
        chosen = papers(cfg)
    else:
        chosen = []                     # ls, trace, status: useful before there is a paper
    plans = [prepare_paper(cfg, idx, p) for p in chosen]
    baseline = ch.load_baseline(cfg)
    current, cites = ch.currents(idx, plans)
    changes = ch.compute(cfg, baseline, current, cites)
    ctx = Context(cfg, idx, states, baseline, plans, changes, project, outcome)
    for pl in plans:
        emit_paper(ctx, pl)
    return ctx


def comparable(path: Path, content: str) -> str:
    """Content with the working-tree-dependent parts (run freshness) removed."""
    if path.suffix == ".tex":
        return "\n".join(ln for ln in content.splitlines() if not ln.startswith(r"\vouch@state{"))
    if path.suffix == ".csv":
        rows = list(csv.reader(io.StringIO(content)))
        if rows and "freshness" in rows[0]:
            i = rows[0].index("freshness")
            for r in rows:
                if len(r) > i:
                    r[i] = ""
        return "\n".join(",".join(r) for r in rows)
    return content


def write_files(ctx: Context) -> tuple[list[Path], list[Path]]:
    written, unchanged = [], []
    for pl in ctx.plans:
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
    return written, unchanged


def build(cfg: Config, *, notify: bool = True) -> BuildResult:
    ctx = plan(cfg, evaluate=True)
    acked = ch.auto_acknowledge(cfg, ctx.baseline, ctx.changes)
    if acked:
        done = {c.key for c in acked}
        ctx.changes = [c for c in ctx.changes if c.key not in done]
        for pl in ctx.plans:          # the CSV reports acknowledgment: emit against the new baseline
            emit_paper(ctx, pl)
    written, unchanged = write_files(ctx)
    if ctx.derived is not None and ctx.derived.written is not None:
        written.insert(0, ctx.derived.written)
    if cfg.get("latex", "annotate", False):
        from .tex.annotate import annotate
        written += annotate(ctx)
    fresh, err = ch.notify(cfg, ctx.changes) if notify else ([], None)
    return BuildResult(ctx, written, unchanged, acked, fresh, err)
