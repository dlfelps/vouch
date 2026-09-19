"""The ``vouch`` command line (SPEC §12)."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

from . import __version__
from . import console as C
from .config import Config, ConfigError, discover_root


class _Lazy:
    """A module imported on first use: most commands (and the edit hook) never need it."""

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr: str):
        from importlib import import_module
        return getattr(import_module(self._name, __package__), attr)


ch = _Lazy(".changes")


def origin(e):
    from .index import origin as f
    return f(e)


def source_runs(e):
    from .index import source_runs as f
    return f(e)

EXIT_OK, EXIT_FAIL, EXIT_UNVERIFIED = 0, 1, 2


def _root(args) -> Path:
    if getattr(args, "root", None):
        return Path(args.root).resolve()
    root, _ = discover_root(Path.cwd())
    return root


def _config(args) -> Config:
    return Config.load(_root(args))


def _fatal(cmd: str, exc: Exception) -> int:
    C.err(f"vouch {cmd}: {exc}")
    return EXIT_UNVERIFIED


def _envelope(command: str, ok: bool, **kw) -> str:
    return json.dumps({"schema": "vouch/1", "command": command, "ok": ok, **kw}, indent=2,
                      default=str)


SYM = {"error": "✗", "warning": "!", "info": "·"}


def _print_issue(i, indent: str = "  ") -> None:
    C.out(f"{indent}{SYM[i.severity]} {i.check:<14} {i.message}")
    pad = indent + " " * 17
    where = i.where_all or ([(i.file, i.line)] if i.file else [])
    if where:
        locs = ", ".join(f"{f}:{ln}" if ln else f for f, ln in where[:6])
        C.out(f"{pad}at {locs}" + (" ..." if len(where) > 6 else ""))
    if i.fix and i.severity != "info":
        C.out(f"{pad}fix: {i.fix}")


# ---------------------------------------------------------------------------
# change blocks
# ---------------------------------------------------------------------------

def _now_false(c) -> bool:
    return c.new.kind == "claim" and not (c.new.raw or {}).get("holds", True)


def _change_rank(c) -> int:
    """What to read first: claims that stopped holding, then suspicious moves."""
    if _now_false(c):
        return 0
    return {"suspicious": 1, "changed": 2, "figure-changed": 3}.get(c.cls, 4)


def _old_new(c) -> tuple[str, str]:
    old = " | ".join(ch.readable(p) for p in (c.old or {}).get("plain", [])) or "?"
    new = " | ".join(ch.readable(p) for p in c.new.plain) or "?"
    return old, new


def print_changes(changes: list, *, heading: bool = True) -> None:
    from .values import natural_key
    pending = [c for c in changes if c.pending]
    if not pending:
        return
    if heading:
        n = len(pending)
        C.out("")
        C.out(f"{n} CHANGED VALUE{'S' if n > 1 else ''} — re-read the sentences below")
    # cells cited only through a \vouchtable are shown together, under their table
    tables: dict[str, list] = {}
    single = []
    for c in pending:
        via = {w.table for w in c.citations}
        if len(via) == 1 and None not in via:
            tables.setdefault(via.pop(), []).append(c)
        else:
            single.append(c)
    items = [(_change_rank(c), natural_key(c.key), c) for c in single]
    items += [(min(_change_rank(c) for c in cells), natural_key(t), (t, cells))
              for t, cells in tables.items()]
    for _, _, item in sorted(items, key=lambda x: (x[0], x[1])):
        C.out("")
        if isinstance(item, tuple):
            _print_table_changes(*item)
            continue
        c = item
        label = "NOW FALSE" if _now_false(c) else {"suspicious": "SUSPICIOUS", "changed": "CHANGED",
                                                   "figure-changed": "FIGURE"}[c.cls]
        old, new = _old_new(c)
        extra = "; ".join(x for x in (ch.describe_delta(c), "; ".join(c.reasons)) if x)
        if c.cls == "figure-changed":
            C.out(f"  {label:<10}  {c.key}   the figure file changed")
        else:
            C.out(f"  {label:<10}  {c.key}   {old} → {new}" + (f"   ({extra})" if extra else ""))
        for w in c.citations[:8]:
            C.out(f"    {w.file}:{w.line}  \"{w.sentence}\"")
    C.out("")
    C.out("  → fix any sentence that is now wrong, then: vouch ack <key>…  or  vouch review")


def _print_table_changes(table: str, cells: list) -> None:
    from .values import natural_key
    cells = sorted(cells, key=lambda c: natural_key(c.key))
    flagged = sum(c.cls == "suspicious" for c in cells)
    label = "SUSPICIOUS" if flagged else "CHANGED"
    C.out(f"  {label:<10}  table {table}   {len(cells)} cell{'s' if len(cells) > 1 else ''} "
          f"changed" + (f", {flagged} suspicious" if flagged else ""))
    names = [c.key[len(table) + 1:] if c.key.startswith(table + ".") else c.key for c in cells]
    w = max(len(n) for n in names)
    for name, c in zip(names, cells):
        old, new = _old_new(c)
        why = f"   ({'; '.join(c.reasons)})" if c.cls == "suspicious" and c.reasons else ""
        C.out(f"    {'!' if c.cls == 'suspicious' else ' '} {name:<{w}}  {old} → {new}{why}")
    seen = []
    for c in cells:
        for cit in c.citations:
            if (cit.file, cit.line) not in [(s.file, s.line) for s in seen]:
                seen.append(cit)
    for cit in seen[:8]:
        C.out(f"    {cit.file}:{cit.line}  \"{cit.sentence}\"")


# ---------------------------------------------------------------------------
# init / build / export
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    from .init import InitError, init
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    try:
        notes = init(root, args.paper)
    except (InitError, ConfigError) as exc:
        return _fatal("init", exc)
    for n in notes:
        C.out(f"  {n}")
    if args.hook:
        from .hooks import HookError, install
        try:
            C.out(f"  installed pre-commit hook: {install(root)}")
        except HookError as exc:
            C.out(f"  ! pre-commit hook not installed: {exc}")
    if args.agents is not None:
        rc = _init_agents(root, args)
        if rc != EXIT_OK:
            return rc
    C.out("next: record values in your experiments, cite them as \\vouch{key}, run `vouch build`")
    return EXIT_OK


def _init_agents(root: Path, args) -> int:
    from . import agents
    parts = [p.strip() for p in (args.agents or ",".join(agents.PARTS)).split(",") if p.strip()]
    bad = [p for p in parts if p not in agents.PARTS + agents.OPTIONAL]
    if bad:
        return _fatal("init", ValueError(f"--agents takes {','.join(agents.PARTS + agents.OPTIONAL)}, "
                                         f"not {bad}"))
    changes = agents.planned(root, parts, stop_gate=args.stop_gate)
    if not changes:
        C.out("  agents: already set up")
        return EXIT_OK
    C.out(agents.diff(root, changes).rstrip("\n"))
    if not args.yes:
        if not sys.stdin.isatty():
            C.out("  agents: nothing written; re-run with --yes to write the files above")
            return EXIT_OK
        if input("write these files? [y/N] ").strip().lower() not in ("y", "yes"):
            C.out("  agents: nothing written")
            return EXIT_OK
    agents.write(changes)
    for p in changes:
        C.out(f"  wrote {p.relative_to(root).as_posix()}")
    return EXIT_OK


def _run_build(cfg: Config, quiet: bool = False, notify: bool = True):
    from .build import build
    res = build(cfg, notify=notify)
    return res


def cmd_build(args) -> int:
    from .build import BuildError
    try:
        cfg = _config(args)
        res = _run_build(cfg, notify=not args.no_notify)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("build", exc)
    if args.json:
        print(_envelope("build", True, papers=[
            {"main": cfg.rel(pl.main), "values_file": cfg.rel(pl.values_path),
             "provenance_csv": cfg.rel(pl.csv_path) if pl.csv_path else None, "counts": pl.counts,
             "issues": [i.to_json() for i in pl.issues]} for pl in res.plans],
            issues=[i.to_json() for i in res.issues],
            written=sorted(cfg.rel(p) for p in res.written),
            changes=[c.to_json() for c in res.ctx.changes if c.pending],
            acknowledged=[c.to_json() for c in res.auto_acked]))
        return EXIT_OK
    written = set(res.written)
    dv = res.ctx.derived
    if dv is not None and dv.evaluated:
        n = len((res.ctx.idx.derived_doc or {}).get("definitions") or {})
        C.out(f"vouch build: evaluated {', '.join(dv.modules)} ({n} definition{'s' if n != 1 else ''})"
              f" -> {cfg.rel(cfg.store / 'derived.json')}"
              + ("" if dv.written else " (unchanged)"))
    elif dv is not None and dv.removed is not None:
        C.out(f"vouch build: removed {cfg.rel(dv.removed)} (no values modules)")
    for pl in res.plans:
        c = pl.counts
        touched = [p for p in pl.files if p in written]
        C.out(f"vouch build: {cfg.rel(pl.main)}")
        C.out(f"  {'wrote' if touched else 'unchanged'}: {cfg.rel(pl.values_path)} "
              f"({c['values']} values, {c['claims']} claims, {c['tables']} tables; "
              f"{c['citations']} citations)"
              + (f", {cfg.rel(pl.csv_path)}" if pl.csv_path else ""))
        for i in pl.issues:
            _print_issue(i)
    for i in res.issues:
        _print_issue(i)
    hidden = [a for a in res.auto_acked if a.cls == "hidden"]
    for a in hidden:
        C.out(f"  · hidden         {a.key} moved within the printed rounding "
              f"({ch.describe_delta(a) or 'tiny change'}); logged to .vouch/history.jsonl")
    print_changes(res.ctx.changes)
    if res.notify_error:
        C.out(f"  ! {res.notify_error}")
    return EXIT_OK


def cmd_export(args) -> int:
    from .build import BuildError, papers, plan
    from .provenance import csv_text, rows
    try:
        cfg = _config(args)
        chosen = papers(cfg)
        if args.paper:
            chosen = [p for p in chosen if p["main"] == args.paper] or [{"main": args.paper}]
        ctx = plan(cfg, only=chosen[:1])
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("export", exc)
    pl = ctx.plans[0]
    if args.json:
        print(_envelope("export", True, rows=rows(pl.doc, ctx.idx, pl.rendered, ctx, args.all)))
        return EXIT_OK
    text = csv_text(pl.doc, ctx.idx, pl.rendered, ctx, include_uncited=args.all)
    if args.csv in (None, "-"):
        sys.stdout.write(text)
    else:
        Path(args.csv).write_text(text, encoding="utf-8", newline="\n")
        C.out(f"wrote {args.csv} ({text.count(chr(10)) - 1} rows)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# check / status
# ---------------------------------------------------------------------------

def cmd_check(args) -> int:
    from .build import BuildError
    from .check import run_check
    try:
        cfg = _config(args)
        rep = run_check(cfg, strict=args.strict, check_env=not args.no_env)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        if args.json:
            print(_envelope("check", False, error=str(exc), issues=[]))
        return _fatal("check", exc)
    s = rep.summary
    if args.json:
        print(_envelope("check", rep.ok, summary={**s, "errors": len(rep.errors),
                                                  "warnings": len(rep.warnings)},
                        issues=[i.to_json() for i in rep.issues]))
        return EXIT_OK if rep.ok else EXIT_FAIL
    shown = [i for i in rep.issues if i.severity != "info" or args.verbose]
    if not args.quiet or shown:
        C.out(f"vouch check: {', '.join(s['papers'])} · {s['citations']} citations · "
              f"{s['runs_fresh']}/{s['runs_cited']} cited runs fresh")
    for i in shown:
        _print_issue(i)
    infos = sum(1 for i in rep.issues if i.severity == "info")
    if infos and not args.verbose and not args.quiet:
        C.out(f"  · {infos} note(s) (--verbose to show)")
    if rep.ok:
        if not args.quiet:
            w = len(rep.warnings)
            C.out(f"{SYM_OK} OK" + (f" ({w} warning{'s' if w != 1 else ''})" if w else ""))
        return EXIT_OK
    C.out(f"FAILED: {len(rep.errors)} error(s), {len(rep.warnings)} warning(s)")
    return EXIT_FAIL


SYM_OK = "✓"


def cmd_status(args) -> int:
    from .build import BuildError, plan
    from .check import _cited
    try:
        cfg = _config(args)
        ctx = plan(cfg, need_paper=False, check_env=not args.no_env)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("status", exc)
    cited = _cited(ctx)
    if args.json:
        print(_envelope("status", True, runs=[
            {"run": r, "state": st.state, "summary": st.summary(), "command": st.command,
             "cited": len(cited.get(r, [])), "reasons": [vars(x) for x in st.reasons]}
            for r, st in sorted(ctx.states.items())]))
        return EXIT_OK
    counts: dict[str, int] = {}
    for st in ctx.states.values():
        counts[st.state] = counts.get(st.state, 0) + 1
    C.out("runs: " + " · ".join(f"{n} {k}" for k, n in sorted(counts.items())) if counts
          else "runs: none recorded yet")
    for r, st in sorted(ctx.states.items()):
        sym = "✗" if st.is_error else ("~" if st.state == "accepted" else "✓")
        rec = st.recorded or {}
        when = str(rec.get("started", ""))[:16].replace("T", " ")
        n = len(cited.get(r, []))
        C.out(f"  {sym} {r:<24} {st.state:<15} {when} · {rec.get('duration_s', '?')} s"
              + (f" · cited {n}×" if n else " · not cited"))
        if st.state not in ("fresh",):
            C.out(f"    {'':<24} {st.summary()}")
        if st.is_error and st.command:
            C.out(f"    {'':<24} re-run: {st.command}")
        for d in st.of("env-drift"):
            C.out(f"    {'':<24} ! {d.detail}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# ls / trace
# ---------------------------------------------------------------------------

def _match(pattern: str | None, key: str) -> bool:
    if not pattern:
        return True
    if any(c in pattern for c in "*?["):
        return fnmatch.fnmatchcase(key, pattern)
    return pattern.lower() in key.lower()


def _state_of(ctx, e) -> str:
    """The state of the runs a value rests on: the worst of them, for a derived value."""
    from .provenance import _freshness
    return _freshness(ctx, source_runs(e))


def _feeds(ctx, key: str) -> list[str]:
    """Derived keys whose definitions read ``key`` (or something under it)."""
    idx = ctx.idx
    out = []
    for dk, d in sorted(((idx.derived_doc or {}).get("definitions") or {}).items()):
        for dep in d.get("deps") or {}:
            e = idx.get(dep)
            real = e.extra.get("alias_of", dep) if e is not None else dep
            if real == key or real.startswith(key + ".") or dep == key:
                out.append(f"{dk} ({d.get('kind', 'value') if d.get('kind') != 'value' else 'derived'})")
                break
    return out


def cmd_ls(args) -> int:
    from .build import BuildError, plan
    try:
        cfg = _config(args)
        ctx = plan(cfg, need_paper=False, check_env=False)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("ls", exc)
    counts: dict[str, int] = {}
    for pl in ctx.plans:
        for c in pl.doc.citations:
            counts[c.key] = counts.get(c.key, 0) + 1
            if c.kind == "table" and c.key in ctx.idx.tables:      # a cited table cites its cells
                t = ctx.idx.tables[c.key]
                for i in range(len(t.rows)):
                    for col in t.columns:
                        ck = t.cell_key(i, col)
                        counts[ck] = counts.get(ck, 0) + 1
    rendered = ctx.plans[0].rendered if ctx.plans else {}
    rows = []
    from .values import natural_key
    for key in sorted(ctx.idx.entries, key=natural_key):
        e = ctx.idx.entries[key]
        if not _match(args.pattern, key):
            continue
        n = counts.get(key, 0)
        if (args.cited and not n) or (args.uncited and n):
            continue
        r = rendered.get((key, ""))
        if r is None and e.kind not in ("claim", "table"):       # no paper yet: render here
            from .render import Options, RenderError, render
            try:
                r = render(e.raw, e.fmt, unit=e.unit, opts=Options.from_config(cfg))
            except (RenderError, ValueError, TypeError):
                r = None
        rows.append({"key": key, "kind": e.kind,
                     "value": ch.readable(r.plain) if r else ("HOLDS" if e.raw else "FALSE")
                     if e.kind == "claim" else "",
                     "desc": e.desc or "", "run": e.run or "", "origin": origin(e),
                     "state": _state_of(ctx, e),
                     "cited": n, "fmt": e.fmt or "", "unit": e.unit or "", "better": e.better or ""})
    if args.json:
        print(_envelope("ls", True, keys=rows))
        return EXIT_OK
    if not rows:
        C.out("no matching keys")
        return EXIT_OK
    hidden = 0
    if not args.all:        # a mean ± std's .mean/.std/.n/... under it, unless cited or asked for
        listed = {r["key"] for r in rows}
        shown = [r for r in rows if not (r["kind"] == "stat-field" and not r["cited"]
                                         and r["key"].rsplit(".", 1)[0] in listed)]
        hidden, rows = len(rows) - len(shown), shown
    w = min(max(len(r["key"]) for r in rows), 40)
    vw = min(max(len(r["value"]) for r in rows), 24)
    for r in rows:
        desc = r["desc"] if len(r["desc"]) <= 60 else r["desc"][:57] + "..."
        C.out(f"{r['key']:<{w}}  {r['value']:<{vw}}  {r['state']:<8} cited {r['cited']:<3} {desc}")
    if hidden:
        C.out(f"({hidden} subfields of mean ± std values not shown: .mean .std .n .ci95 .min "
              f".max; `vouch ls --all` lists them)")
    return EXIT_OK


def cmd_trace(args) -> int:
    from .build import BuildError, plan
    try:
        cfg = _config(args)
        ctx = plan(cfg, need_paper=False, check_env=False)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("trace", exc)
    if args.json:
        from .assist import trace
        got = trace(ctx, args.target)
        print(_envelope("trace", "error" not in got, **got))
        return EXIT_OK if "error" not in got else EXIT_FAIL
    target = args.target
    idx = ctx.idx
    if idx.get(target) is not None:
        return _trace_key(ctx, target, args)
    if ":" in target and target.rsplit(":", 1)[1].isdigit() and target.split(":")[0].endswith(".tex"):
        f, line = target.rsplit(":", 1)
        hits = [c for pl in ctx.plans for c in pl.doc.citations
                if c.line == int(line) and (c.file == f or c.file.endswith("/" + f))]
        if not hits:
            C.out(f"no citations on {target}")
            return EXIT_FAIL
        for c in hits:
            macro = {"value": "vouch", "raw": "vouchraw", "claim": "vouchclaim",
                     "table": "vouchtable", "figure": "includegraphics"}[c.kind]
            C.out(f"{c.file}:{c.line}  \\{macro}{{{c.key}}}"
                  + (f" [{c.fmt}]" if c.fmt else "") + (f"  (via \\{c.via})" if c.via else ""))
            if idx.get(c.key):
                _trace_key(ctx, c.key, args, indent="    ")
        return EXIT_OK
    # a path relative to the current directory, or to the project root
    cands = {cfg.rel(target), Path(target).as_posix().removeprefix("./")}
    rel = next((c for c in cands if (cfg.root / c).exists()), cfg.rel(target))
    from .assist import figure_info
    fig = figure_info(ctx, rel)
    if fig is not None:
        C.out(f"{rel}   figure")
        C.out(f"  saved     {fig['saved_at'] or '-'}   in run {fig['run']} ({fig['state']})")
        C.out(f"  command   {fig['run_command'] or '-'}")
        C.out(f"  when      {str(fig['started'] or '')[:16].replace('T', ' ')} UTC"
              + (f" · git {fig['git'][:7]}" if fig.get("git") else ""))
        C.out(f"  file      {fig['file']}")
        if fig["rerun"]:
            C.out(f"  re-run    {fig['rerun']}")
        C.out("  cited     " + (", ".join(fig["cited_at"]) or "not included by the paper"))
        return EXIT_OK
    runs = [r for r, rec in sorted(idx.runs.items())
            if rec.get("entry") == rel or any(u.split("::")[0] == rel
                                              for u in (rec.get("code") or {}).get("units", {}))]
    if runs:
        for r in runs:
            st = ctx.states[r]
            keys = sorted(k for k, e in idx.entries.items() if e.run == r and e.kind == "value")
            C.out(f"run {r} ({st.state}) uses {rel}: {len(keys)} value(s)")
            for k in keys:
                where = [f"{c.file}:{c.line}" for pl in ctx.plans for c in pl.doc.citations if c.key == k]
                C.out(f"  {k}" + (f"   cited at {', '.join(where)}" if where else "   (not cited)"))
        return EXIT_OK
    sugg = idx.suggest(target)
    C.err(f"vouch trace: {target!r} is not a key, a tex file:line, a figure or a file any run used"
          + (f" (did you mean {', '.join(sugg)}?)" if sugg else ""))
    return EXIT_FAIL


def _trace_key(ctx, key: str, args, indent: str = "") -> int:
    from .build import _num
    idx = ctx.idx
    e = idx.get(key)
    rendered = [(f, r) for pl in ctx.plans for (k, f), r in pl.rendered.items() if k == key]
    shown = ", ".join(f"\"{ch.readable(r.plain)}\"" + (f" [{f}]" if f else "") for f, r in
                      dict(rendered).items()) or "-"
    out = [f"{key} = {_num(e.raw)}   → {shown}" + (f"   (fmt {e.fmt})" if e.fmt else "")]
    if e.desc:
        out.append(f"  desc      {e.desc}" + (f"   · better: {e.better}" if e.better else ""))
    if e.kind == "table-cell":
        out.append(f"  table     {e.parent}")
    if e.extra.get("alias_of"):
        out.append(f"  alias of  {e.extra['alias_of']}")
    if e.kind == "claim" and e.extra.get("explanation"):
        m = e.extra.get("margin")
        out.append(f"  because   {e.extra['explanation']}"
                   + (f"   (margin {m:.1%})" if isinstance(m, (int, float)) else ""))
    d = e.extra.get("derived")
    if d:
        out.append(f"  derived   {d.get('function', '?')}   at {d.get('site', '?')}")
        deps = [k for k in d.get("deps") or [] if not k.startswith("keys:")]
        for k in deps[:12]:
            de = idx.get(k)
            out.append(f"  from      {k} = {_num(de.raw) if de is not None else '?'}")
        if len(deps) > 12:
            out.append(f"            (+{len(deps) - 12} more)")
        for path in sorted(d.get("inputs") or {}):
            out.append(f"  input     {path}")
        runs = d.get("runs") or []
        if runs:
            out.append("  runs      " + ", ".join(
                f"{r} ({ctx.states[r].state if r in ctx.states else '?'})" for r in runs))
    if e.run:
        rec = idx.runs.get(e.run) or {}
        st = ctx.states.get(e.run)
        imp = rec.get("imported")
        if imp:
            out.append(f"  imported  from {imp.get('file')} into run {e.run} (not recorded live)"
                       + (f"; producers {', '.join(imp.get('producers') or [])}"
                          if imp.get("producers") else "; no producer declared"))
        else:
            out.append(f"  recorded  {e.site or '-'}   in run {e.run}")
        if e.extra.get("call"):
            from .tracked import call_text, per_call_text, timing_text
            call = e.extra["call"]
            out.append(f"  by        {call_text(call)}"
                       + (f"   (listed in vouch.toml {call['via']})" if call.get("via") else ""))
            if call.get("seconds"):
                out.append(f"  time      {timing_text(call)}")
            each = per_call_text(call, limit=len(call.get("results") or []), times=True)
            if each:
                out.append(f"  each call {each}")
            if call.get("sites"):
                out.append(f"  called at {', '.join(call['sites'][:6])}")
        out.append(f"  command   {' '.join(rec.get('command') or []) or '-'}"
                   + ("   (declared, not observed)" if (rec.get("imported") or {}).get("command")
                      else ""))
        git = rec.get("git") or {}
        pk = rec.get("env", {}).get("packages", {})
        out.append(f"  when      {str(rec.get('started', ''))[:16].replace('T', ' ')} UTC · "
                   f"{rec.get('duration_s', '?')} s"
                   + (f" · git {git.get('commit', '')[:7]} ({'dirty' if git.get('dirty') else 'clean'})"
                      if git else "")
                   + f" · python {rec.get('env', {}).get('python', '?')}"
                   + (", " + ", ".join(f"{k} {v}" for k, v in list(pk.items())[:4]) if pk else ""))
        units = (rec.get("code") or {}).get("units", {})
        files = sorted({u.split("::")[0] for u in units})
        code_info = rec.get("code") or {}
        gran = code_info.get("granularity", "?")
        out.append(f"  code      {len(units)} units in {len(files)} file(s), tracked by {gran}"
                   + (f" ({code_info['why']})" if code_info.get("why") else "")
                   + f" · {st.state if st else '?'}" + ("" if args.code else "   (--code to list)"))
        for path, why in sorted((code_info.get("whole_files") or {}).items()):
            out.append(f"            {path}: tracked whole -- {why}")
        if args.code:
            changed = {r.subject for r in (st.reasons if st else [])}
            for u in sorted(units):
                out.append(f"              {'CHANGED ' if u in changed else ''}{u}")
        for path in sorted(rec.get("inputs") or {}):
            bad = st and any(r.subject == path for r in st.reasons)
            out.append(f"  input     {path} · {'CHANGED' if bad else 'unchanged'}")
    feeds = _feeds(ctx, e.extra.get("alias_of", key))
    if feeds:
        out.append(f"  feeds     {' · '.join(feeds)}")
    where = [f"{c.file}:{c.line}" for pl in ctx.plans for c in pl.doc.citations
             if c.key == key or (c.kind == "table" and e.parent == c.key)]
    out.append(f"  cited     {', '.join(dict.fromkeys(where)) or '(not cited)'}")
    change = ctx.pending.get(key)
    if change:
        out.append(f"  pending   {ch.was_text(change)} -- re-read, then vouch ack {key}")
    for line in out:
        C.out(indent + line)
    return EXIT_OK


def cmd_explore(args) -> int:
    from .explore import page, registry, serve
    try:
        cfg = _config(args)
        if args.json or args.html:
            data = registry(cfg)
    except (ConfigError, FileNotFoundError) as exc:
        return _fatal("explore", exc)
    if args.json:
        print(_envelope("explore", True, registry=data))
        return EXIT_OK
    if args.html:
        Path(args.html).write_text(page(data), encoding="utf-8", newline="\n")
        n = data["counts"]
        C.out(f"wrote {args.html} ({n['values']} values, {n['claims']} claims, {n['tables']} tables); "
              f"a snapshot -- `vouch explore` serves a live view")
        return EXIT_OK
    serve(cfg, port=args.port, open_browser=args.open, out=C.out)
    return EXIT_OK


def _ctx(args, cmd: str):
    from .build import BuildError, plan
    try:
        cfg = _config(args)
        return cfg, plan(cfg, need_paper=False, check_env=False)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        raise _Fatal(cmd, exc) from None


class _Fatal(Exception):
    def __init__(self, cmd: str, exc: Exception):
        super().__init__(str(exc))
        self.cmd, self.exc = cmd, exc


def cmd_search(args) -> int:
    from .assist import search
    try:
        cfg, ctx = _ctx(args, "search")
    except _Fatal as f:
        return _fatal(f.cmd, f.exc)
    hits = search(ctx, " ".join(args.words), limit=args.limit)
    if args.json:
        print(_envelope("search", True, query=" ".join(args.words), results=hits))
        return EXIT_OK
    if not hits:
        C.out("no matching keys (vouch ls lists them all; vouch explore browses them)")
        return EXIT_FAIL
    w = min(max(len(h["key"]) for h in hits), 44)
    v = min(max(len(h["value"]) for h in hits), 18)
    for h in hits:
        tail = ", ".join(x for x in (h["origin"], h["state"] if h["state"] not in ("fresh", "cosmetic")
                                     else "") if x)
        C.out(f"{h['key']:<{w}}  {h['value']:<{v}}  {h['desc'][:60]}" + (f"   ({tail})" if tail else ""))
    return EXIT_OK


def cmd_cite(args) -> int:
    from .assist import cite
    try:
        cfg, ctx = _ctx(args, "cite")
    except _Fatal as f:
        return _fatal(f.cmd, f.exc)
    info = cite(ctx, args.key)
    if args.fmt and "error" not in info and info.get("kind") not in ("claim", "table", "pending"):
        from .assist import shown, snippet
        from .render import Options
        e = ctx.idx.get(args.key)
        info["snippets"] = [{"latex": snippet(e, args.fmt),
                             "renders": shown(e, Options.from_config(cfg), args.fmt), "fmt": args.fmt}]
    if args.json:
        print(_envelope("cite", "error" not in info, **info))
        return EXIT_OK if "error" not in info else EXIT_FAIL
    if "error" in info:
        C.err(f"vouch cite: {info['error']}" + (f" (did you mean {', '.join(info['suggestions'])}?)"
                                                if info.get("suggestions") else ""))
        return EXIT_FAIL
    w = max(len(x["latex"].split("\n")[0]) for x in info["snippets"])
    for x in info["snippets"]:
        first = x["latex"].split("\n")[0]
        if "\n" in x["latex"]:
            C.out(x["latex"])
            C.out(f"{'':<{w}}  ({x['renders']})")
            continue
        C.out(f"{first:<{w}}  →  {x['renders']}" + (f"    (fmt {x['fmt']})" if x.get("fmt") else ""))
    about = [info.get("desc") or ""]
    if info.get("better"):
        about.append(f"{info['better']} is better")
    if info.get("because"):
        about.append(f"because {info['because']}" + (f" (margin {info['margin']:.1%})"
                                                       if isinstance(info.get("margin"), (int, float)) else ""))
    if info.get("kind") == "claim":
        about.append("HOLDS" if info.get("holds") else "FALSE -- rewrite the claim")
    for f in ("state", "origin"):
        if info.get(f):
            about.append(("run " if f == "origin" and info[f] != "derived" else "") + info[f])
    if info.get("alias_of"):
        about.append(f"alias of {info['alias_of']}")
    if info.get("producer"):
        about.append(f"pending; produce it with: {info['producer']}")
    C.out(" · ".join(a for a in about if a))
    if info.get("subfields"):
        C.out("subfields: " + " · ".join(f".{s['key'].rsplit('.', 1)[1]} {s['renders']}"
                                         for s in info["subfields"]))
    return EXIT_OK


def cmd_compare(args) -> int:
    from .assist import compare, write_definitions
    try:
        cfg, ctx = _ctx(args, "compare")
    except _Fatal as f:
        return _fatal(f.cmd, f.exc)
    r = compare(ctx, args.a, args.b)
    if "error" in r:
        if args.json:
            print(_envelope("compare", False, **r))
        else:
            C.err(f"vouch compare: {r['error']}" + (f" (did you mean {', '.join(r['suggestions'])}?)"
                                                     if r.get("suggestions") else ""))
        return EXIT_FAIL
    written = clash = None
    if args.write:
        path, clash = write_definitions(cfg, r["code"], [r["derive_key"], r["claim_key"]])
        written = None if clash else cfg.rel(path)
    if args.json:
        print(_envelope("compare", True, **r, written=written, already_defined=clash or []))
        return EXIT_OK
    head = f"{r['a']} {r['shown_a']} vs {r['b']} {r['shown_b']}"
    if r.get("winner"):
        head += f"   ({r['better']} is better → {r['winner']} is better)"
    C.out(head)
    parts = [f"difference {r['difference']:+.4g}"]
    if "pts" in r["derive_key"]:
        parts[0] += f" ({100 * r['difference']:.3g} points)"
    if r.get("ratio") is not None:
        parts.append(f"ratio {r['ratio']:.4g}")
    if r.get("relative") is not None:
        parts.append(f"relative {r['relative']:+.1%}")
    C.out("  " + " · ".join(parts))
    if "pooled_std_apart" in r:
        line = f"  Stat: {r['pooled_std_apart']:.3g} pooled std apart"
        wl = r.get("welch")
        if wl:
            p = wl["p"]
            line += f" · Welch t-test p {'< 1e-6' if p < 1e-6 else '= ' + format(p, '.3g')} " \
                    f"(n = {wl['n'][0]}, {wl['n'][1]})"
            if p >= 0.05:
                line += " -- not significant at 0.05: don't write \"significantly\""
        C.out(line)
    if written:
        C.out(f"wrote {r['derive_key']} and {r['claim_key']} to {written}; run `vouch build`")
    elif clash:
        C.out(f"not written: {', '.join(clash)} already defined in "
              f"{cfg.get('python', 'values_modules', ['vouch_values.py'])[0]}")
    else:
        C.out("paste into vouch_values.py (or run again with --write):")
        for ln in r["code"].rstrip("\n").split("\n"):
            C.out("  " + ln)
    C.out("then cite:")
    C.out("  " + r["cite"])
    return EXIT_OK


def cmd_suggest(args) -> int:
    from .tex.suggest import apply, suggestions
    try:
        cfg, ctx = _ctx(args, "suggest")
    except _Fatal as f:
        return _fatal(f.cmd, f.exc)
    if not ctx.plans:
        return _fatal("suggest", ValueError("no [[paper]] in vouch.toml"))
    only = cfg.rel(Path(args.file).resolve()) if args.file else None
    all_sugs = []
    for pl in ctx.plans:
        all_sugs += [(pl, s) for s in suggestions(pl.doc, ctx.idx, cfg, only)]
    if args.json:
        applied = {}
        if args.apply:
            for pl in ctx.plans:
                applied.update({cfg.rel(p): n for p, n in
                                apply(pl.doc, [s for q, s in all_sugs if q is pl], cfg.root).items()})
        print(_envelope("suggest", True, literals=[s.to_json() for _, s in all_sugs], applied=applied))
        return EXIT_OK
    for _, s in all_sugs:
        loc = f"{s.lit.file}:{s.lit.line}"
        lit = s.lit.text
        if s.status == "replace":
            C.out(f"{loc:<22} {lit:<12} → {s.snippet}   exact")
        elif s.status == "ambiguous":
            C.out(f"{loc:<22} {lit:<12} → ambiguous: {', '.join(s.keys[:4])} -- choose by hand")
        else:
            C.out(f"{loc:<22} {lit:<12} → NO SOURCE -- no recorded value prints as {lit}"
                  + (f" (nearest: {s.near})" if s.near else ""))
    counts = {k: sum(1 for _, s in all_sugs if s.status == k) for k in ("replace", "ambiguous", "no-source")}
    C.out(f"{len(all_sugs)} literal(s): {counts['replace']} replaceable"
          + (" (--apply)" if not args.apply else "") + f", {counts['no-source']} no-source, "
          f"{counts['ambiguous']} ambiguous")
    if args.apply:
        total = 0
        for pl in ctx.plans:
            for p, n in apply(pl.doc, [s for q, s in all_sugs if q is pl], cfg.root).items():
                C.out(f"  rewrote {n} literal(s) in {cfg.rel(p)}")
                total += n
        if total:
            C.out("next: vouch build")
    return EXIT_OK


def cmd_todo(args) -> int:
    from .assist import todo
    try:
        cfg, ctx = _ctx(args, "todo")
    except _Fatal as f:
        return _fatal(f.cmd, f.exc)
    items = todo(ctx)
    if args.json:
        print(_envelope("todo", not items, owed=items))
        return EXIT_OK
    if not items:
        C.out("nothing pending: every vouch.expect(...) has been recorded")
        return EXIT_OK
    C.out(f"{len(items)} value(s) the paper is still owed:")
    for it in items:
        C.out(f"  {it['key']}   {it['desc']}")
        C.out(f"      run: {it['producer'] or '(no producer given)'}")
        if it["cited"]:
            C.out(f"      cited at {', '.join(it['cited'][:6])}")
        if it["blocks"]:
            C.out(f"      also blocks {', '.join(it['blocks'])}")
    return EXIT_OK


def cmd_mcp(args) -> int:
    from .mcp_server import serve
    return serve(Path(args.root).resolve() if args.root else None)


def cmd_catalog(args) -> int:
    from .build import BuildError, plan
    from .catalog import write_catalog
    try:
        cfg = _config(args)
        ctx = plan(cfg, need_paper=False, check_env=False)
        written = write_catalog(ctx)
    except (BuildError, ConfigError, FileNotFoundError, OSError) as exc:
        return _fatal("catalog", exc)
    C.out(f"vouch catalog: {'wrote ' + ', '.join(cfg.rel(p) for p in written) if written else 'unchanged'}")
    return EXIT_OK


def cmd_sync(args) -> int:
    from .build import BuildError, plan
    from .tex.annotate import annotate
    try:
        cfg = _config(args)
        ctx = plan(cfg, check_env=False)
        changed = annotate(ctx, strip=args.strip)
    except (BuildError, ConfigError, FileNotFoundError, OSError) as exc:
        return _fatal("sync", exc)
    verb = "stripped annotations from" if args.strip else "annotated"
    if changed:
        C.out(f"vouch sync: {verb} {', '.join(cfg.rel(p) for p in changed)}")
    else:
        C.out("vouch sync: nothing to change")
    if not args.strip and not cfg.get("latex", "annotate", False):
        C.out("  (set [latex] annotate = true to have every `vouch build` keep them current)")
    return EXIT_OK


def cmd_run(args) -> int:
    from .runner import RunError, run_command
    cmd = list(args.cmd)
    if not cmd:
        return _fatal("run", ValueError("no command: vouch run ID [--dep P]... -- CMD ..."))
    argv = [args.id]
    for flag, vals in (("--dep", args.dep), ("--input", args.input), ("--out", args.out)):
        for v in vals or []:
            argv += [flag, v]
    for flag, v in (("--values", args.values), ("--prefix", args.prefix), ("--row-key", args.row_key)):
        if v:
            argv += [flag, v]
    if args.stats:
        argv.append("--stats")
    argv += ["--", *cmd]
    try:
        cfg = _config(args)
        return run_command(cfg, args.id, cmd, deps=args.dep or [], inputs=args.input or [],
                           outs=args.out or [], values=args.values, prefix=args.prefix,
                           row_key=args.row_key, stats=args.stats, argv=argv, out=C.out)
    except (RunError, ConfigError, OSError, ValueError) as exc:
        return _fatal("run", exc)


def cmd_import(args) -> int:
    from .runner import RunError, import_file
    try:
        cfg = _config(args)
        rec = import_file(cfg, args.file, args.run, prefix=args.prefix, producers=args.producer or [],
                          command=args.command, row_key=args.row_key, stats=args.stats)
    except (RunError, ConfigError, OSError, ValueError) as exc:
        return _fatal("import", exc)
    vals = rec["values"]
    described = sum(1 for k, v in vals.items() if v.get("desc") or cfg.metric_defaults(k).get("desc"))
    code = rec["code"]
    n_files = len(code["files"]) + len([p for p in rec["inputs"] if p != rec["imported"]["file"]])
    C.out(f"imported {len(vals)} value(s) into run {rec['run']}"
          + (f" (prefix {args.prefix})" if args.prefix else "")
          + f" · granularity: declared ({n_files} file{'s' if n_files != 1 else ''})")
    C.out(f"  described: {described}/{len(vals)} (desc= or [metrics] patterns)")
    if not args.producer:
        C.out("  ! no --producer: nothing ties these numbers to code; check will warn until they "
              "are re-recorded by a real run")
    C.out("  note: imported, not recorded live; freshness tracks the declared producer files "
          "and the results file")
    return EXIT_OK


# ---------------------------------------------------------------------------
# accept / changes / ack / review
# ---------------------------------------------------------------------------

def cmd_accept(args) -> int:
    from .build import BuildError, build, plan
    from .freshness import acceptable_hashes, load_ledger, now, who, write_ledger
    try:
        cfg = _config(args)
        ctx = plan(cfg, check_env=False)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("accept", exc)
    st = ctx.states.get(args.run)
    if st is None:
        return _fatal("accept", ValueError(f"no run {args.run!r}; see `vouch status`"))
    hashes = acceptable_hashes(st)
    if not hashes:
        C.out(f"run {args.run} is {st.state}; nothing to accept"
              + (" (tampered artifacts and upstream staleness can't be accepted: re-run)"
                 if st.state in ("tampered", "upstream-stale") else ""))
        return EXIT_OK if st.state in ("fresh", "cosmetic", "accepted") else EXIT_FAIL
    ledger = load_ledger(cfg)
    ledger[args.run] = {"run": args.run, "why": args.why, "by": who(), "date": now(),
                        "hashes": hashes}
    write_ledger(cfg, ledger)
    C.out(f"accepted run {args.run}: {st.summary()}")
    C.out(f"  why: {args.why}")
    C.out("  recorded in .vouch/accepted.toml; the next edit to the same code re-opens it")
    build(cfg, notify=False)
    return EXIT_OK


def _pending(cfg: Config):
    from .build import plan
    ctx = plan(cfg, check_env=False)
    return ctx, [c for c in ctx.changes if c.pending]


def cmd_changes(args) -> int:
    from .build import BuildError
    try:
        cfg = _config(args)
        ctx, pending = _pending(cfg)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("changes", exc)
    if args.json:
        print(_envelope("changes", not pending, changes=[c.to_json() for c in pending],
                        automatic=[c.to_json() for c in ctx.changes if not c.pending]))
        return EXIT_OK
    if args.md:
        Path(args.md).write_text(_changes_md(pending), encoding="utf-8", newline="\n")
        C.out(f"wrote {args.md} ({len(pending)} change(s))")
        return EXIT_OK
    if not pending:
        C.out("no pending changes: every cited value is as last acknowledged")
    print_changes(pending)
    auto = [c for c in ctx.changes if not c.pending]
    if auto:
        C.out(f"  · {len(auto)} automatic ({', '.join(sorted({c.cls for c in auto}))}); "
              f"acknowledged at the next `vouch build`")
    return EXIT_OK


def _changes_md(pending) -> str:
    lines = ["# Changed values in the paper", "",
             f"{len(pending)} cited value(s) changed since they were last acknowledged. "
             "Re-read each sentence below; fix any that the new value makes wrong.", ""]
    for c in pending:
        old = " \\| ".join(ch.readable(p) for p in (c.old or {}).get("plain", [])) or "?"
        new = " \\| ".join(ch.readable(p) for p in c.new.plain) or "?"
        lines.append(f"## `{c.key}` — {c.cls}")
        lines.append("")
        lines.append(f"- was **{old}**, now **{new}**" + (f" ({ch.describe_delta(c)})"
                                                         if ch.describe_delta(c) else ""))
        if c.reasons:
            lines.append(f"- possible problem: {'; '.join(c.reasons)}")
        lines.append(f"- last acknowledged {str((c.old or {}).get('acked', ''))[:10]} by "
                     f"{(c.old or {}).get('by', '?')}")
        lines.append("")
        for w in c.citations:
            lines.append(f"> `{w.file}:{w.line}` — {w.sentence}")
            lines.append("")
    return "\n".join(lines)


def cmd_ack(args) -> int:
    from .build import BuildError, build
    try:
        cfg = _config(args)
        ctx, pending = _pending(cfg)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("ack", exc)
    if args.all:
        chosen = pending
    elif args.run:
        chosen = [c for c in pending if c.new.source == f"run:{args.run}"]
    else:
        wanted = set(args.keys)

        def hits(c, w: str) -> bool:        # a key, a glob, or a table: all its changed cells
            return c.key == w or fnmatch.fnmatchcase(c.key, w) or \
                any(cit.table == w for cit in c.citations)
        chosen = [c for c in pending if any(hits(c, w) for w in wanted)]
        unknown = {w for w in wanted if not any(hits(c, w) for c in chosen)}
        if unknown:
            C.err(f"vouch ack: no pending change for {', '.join(sorted(unknown))} "
                  f"(see `vouch changes`)")
            if not chosen:
                return EXIT_FAIL
    if not chosen:
        C.out("nothing to acknowledge")
        return EXIT_OK
    ch.acknowledge(cfg, ctx.baseline, chosen, args.why)
    for c in chosen:
        C.out(f"  acknowledged {c.key}")
    build(cfg, notify=False)
    C.out(f"{len(chosen)} change(s) acknowledged; generated files rebuilt")
    return EXIT_OK


def _open_editor(root: Path, file: str, line: int) -> None:
    import shutil
    import subprocess
    path = str(root / file)
    code = shutil.which("code")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    try:
        if editor:
            subprocess.run([*editor.split(), f"+{line}", path])
        elif code:
            subprocess.run([code, "-g", f"{path}:{line}"])
        else:
            C.out(f"    (set $EDITOR to open files; the sentence is at {file}:{line})")
    except OSError as exc:
        C.out(f"    could not open an editor: {exc}")


def cmd_review(args) -> int:
    from .build import BuildError, build
    if not sys.stdin.isatty():
        return _fatal("review", RuntimeError("review is interactive; use `vouch changes` and "
                                             "`vouch ack KEY` in scripts"))
    try:
        cfg = _config(args)
        ctx, pending = _pending(cfg)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("review", exc)
    if not pending:
        C.out("no pending changes")
        return EXIT_OK
    chosen = []
    for n, c in enumerate(pending, 1):
        C.out(f"\n[{n}/{len(pending)}]")
        print_changes([c], heading=False)
        while True:
            ans = input("  [a]ck  [s]kip  [o]pen  [q]uit > ").strip().lower()[:1]
            if ans == "o" and c.citations:
                _open_editor(cfg.root, c.citations[0].file, c.citations[0].line)
                continue
            break
        if ans == "a":
            chosen.append(c)
        elif ans == "q":
            break
    if chosen:
        why = input("  why (recorded with the acknowledgment) > ").strip() or "reviewed with vouch review"
        ch.acknowledge(cfg, ctx.baseline, chosen, why)
        build(cfg, notify=False)
    C.out(f"{len(chosen)} acknowledged, {len(pending) - len(chosen)} still pending")
    return EXIT_OK


def cmd_hook(args) -> int:
    if args.action in ("claude", "stop"):
        from .edithook import run_edit_hook, run_stop_hook
        data = sys.stdin.read() if not sys.stdin.isatty() else ""
        code, text = (run_edit_hook(data) if args.action == "claude"
                      else run_stop_hook(data, Path(args.root) if args.root else None))
        if text:
            sys.stderr.write(text)
        return code
    from .hooks import HookError, install
    try:
        path = install(_root(args), strict=args.strict, force=args.force)
    except HookError as exc:
        return _fatal("hook install", exc)
    C.out(f"installed {path}: runs `vouch check --quiet{' --strict' if args.strict else ''}` "
          f"before each commit")
    return EXIT_OK


# ---------------------------------------------------------------------------

def make_parser() -> argparse.ArgumentParser:
    import argparse
    p = argparse.ArgumentParser(prog="vouch", description="Every number in your paper, vouched "
                                "for by the code that produced it.")
    p.add_argument("--version", action="version", version=f"vouch {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.add_argument("--root", help="project root (default: nearest vouch.toml)")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "set up vouch.toml, the store and vouch.sty")
    sp.add_argument("--paper", help="the paper's main .tex (default: detected)")
    sp.add_argument("--hook", action="store_true", help="also install the git pre-commit hook")
    sp.add_argument("--agents", nargs="?", const="", metavar="PARTS",
                    help="set up Claude Code: skill,rules,hook,mcp (default: skill,rules,hook)")
    sp.add_argument("--stop-gate", action="store_true",
                    help="with --agents: also block finishing until `vouch check --strict` passes")
    sp.add_argument("--yes", action="store_true", help="write without asking")

    sp = add("build", cmd_build, "render values, tables and the provenance CSV for the paper")
    sp.add_argument("--json", action="store_true", help="machine-readable summary")
    sp.add_argument("--no-notify", action="store_true", help="don't run the on_change hook")

    sp = add("check", cmd_check, "the gate: cited values exist, are fresh, and are acknowledged")
    sp.add_argument("--strict", action="store_true", help="treat warnings as errors (CI, agents)")
    sp.add_argument("--quiet", action="store_true", help="print only problems")
    sp.add_argument("--verbose", action="store_true", help="also print informational notes")
    sp.add_argument("--json", action="store_true", help="the vouch/1 JSON envelope")
    sp.add_argument("--no-env", action="store_true", help="skip the package-version drift check")

    sp = add("status", cmd_status, "freshness of every run, with re-run commands")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-env", action="store_true", help="skip the package-version drift check")

    sp = add("ls", cmd_ls, "list recorded keys")
    sp.add_argument("pattern", nargs="?", help="substring or glob (e.g. 'cifar.*.acc')")
    sp.add_argument("--cited", action="store_true", help="only keys the paper cites")
    sp.add_argument("--uncited", action="store_true", help="only keys the paper doesn't cite")
    sp.add_argument("--all", action="store_true",
                    help="also list each mean ± std's subfields (.mean, .std, .n, ...)")
    sp.add_argument("--json", action="store_true", help="every key, subfields included")

    sp = add("explore", cmd_explore, "browse recorded values in a local web page; copy the LaTeX")
    sp.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (default 8765)")
    sp.add_argument("--open", action="store_true", help="open it in the browser")
    sp.add_argument("--html", metavar="FILE", help="write a self-contained snapshot instead")
    sp.add_argument("--json", action="store_true", help="print the data the page shows")

    sp = add("trace", cmd_trace, "where a value came from (a key, a tex file:line, or a script)")
    sp.add_argument("target")
    sp.add_argument("--code", action="store_true", help="list every code unit")
    sp.add_argument("--json", action="store_true")

    add("catalog", cmd_catalog, "rewrite .vouch/CATALOG.md (every build does too)")

    add("mcp", cmd_mcp, "serve search/cite/compare/check/... to MCP clients over stdio")

    sp = add("search", cmd_search, "find keys by words (key segments, descriptions, arguments)")
    sp.add_argument("words", nargs="+")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--json", action="store_true")

    sp = add("cite", cmd_cite, "the exact LaTeX to cite a key, and what it renders as")
    sp.add_argument("key")
    sp.add_argument("--fmt", help="a format to render it with")
    sp.add_argument("--json", action="store_true")

    sp = add("compare", cmd_compare, "the arithmetic between two values, and the code to cite it")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("--write", action="store_true", help="append the derive/claim to vouch_values.py")
    sp.add_argument("--json", action="store_true")

    sp = add("suggest", cmd_suggest, "replace numbers typed into the paper with citations")
    sp.add_argument("file", nargs="?", help="one tex file (default: every paper)")
    sp.add_argument("--apply", action="store_true", help="rewrite unique exact matches")
    sp.add_argument("--json", action="store_true")

    sp = add("todo", cmd_todo, "values the paper cites that no run has recorded yet (vouch.expect)")
    sp.add_argument("--json", action="store_true")

    sp = add("sync", cmd_sync, "write each cited value into a trailing % vouch: comment")
    sp.add_argument("--strip", action="store_true", help="remove every annotation")

    sp = add("run", cmd_run, "record a run of any command: vouch run ID [--dep P]... -- CMD ...")
    sp.add_argument("id", help="the run id")
    sp.add_argument("--dep", action="append", help="code the run depends on (file or directory)")
    sp.add_argument("--input", action="append", help="data the run reads")
    sp.add_argument("--out", action="append", help="a file the run writes (an artifact)")
    sp.add_argument("--values", help="read values from this results file instead of $VOUCH_VALUES")
    sp.add_argument("--prefix", help="prefix for every key")
    sp.add_argument("--row-key", help="for tabular values: the column naming each row")
    sp.add_argument("--stats", action="store_true", help="lists of numbers become Stats")
    sp.set_defaults(cmd=[])              # everything after `--` (split off in main)

    sp = add("import", cmd_import, "register an existing results file as a run")
    sp.add_argument("file")
    sp.add_argument("--run", required=True, help="the run id")
    sp.add_argument("--prefix", help="prefix for every key")
    sp.add_argument("--producer", action="append", help="code that produced the file (file or dir)")
    sp.add_argument("--command", help="the command that produced it (declared, not observed)")
    sp.add_argument("--row-key", help="for tabular files: the column naming each row")
    sp.add_argument("--stats", action="store_true", help="lists of numbers become Stats")

    sp = add("accept", cmd_accept, "record that a stale run's result still stands")
    sp.add_argument("run")
    sp.add_argument("--why", required=True, help="why the measurement is unaffected")

    sp = add("changes", cmd_changes, "cited values that changed since last acknowledged")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--md", metavar="FILE", help="write a shareable Markdown review report")

    sp = add("ack", cmd_ack, "acknowledge changed values after re-reading their sentences")
    sp.add_argument("keys", nargs="*",
                    help="keys, globs ('learning_curve.*'), or a table: every changed cell of it")
    sp.add_argument("--all", action="store_true", help="every pending change")
    sp.add_argument("--run", help="every pending change from this run")
    sp.add_argument("--why", default="acknowledged", help="recorded with the acknowledgment")

    add("review", cmd_review, "step through pending changes interactively")

    sp = add("hook", cmd_hook, "git pre-commit hook (install), Claude Code hooks (claude, stop)")
    sp.add_argument("action", choices=["install", "claude", "stop"])
    sp.add_argument("--strict", action="store_true", help="the hook runs check --strict")
    sp.add_argument("--force", action="store_true", help="replace an existing pre-commit hook")

    sp = add("export", cmd_export, "write the provenance table")
    sp.add_argument("--csv", help="output path (default: stdout)")
    sp.add_argument("--all", action="store_true", help="include recorded keys the paper doesn't cite")
    sp.add_argument("--paper", help="which paper (main .tex), if there are several")
    sp.add_argument("--json", action="store_true", help="rows as JSON instead of CSV")
    return p


def _utf8_when_piped() -> None:
    """Piped output (CI logs, agents, files) is UTF-8; a console keeps its own handling."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty() and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    # the CLI runs no experiment: nothing to track, no figure to record (vouch_values.py
    # may plot). Started only if vouch was imported by something else first (tests).
    if "vouch.tracing" in sys.modules:
        sys.modules["vouch.tracing"].tracker.stop()
    if "vouch.figures" in sys.modules:
        sys.modules["vouch.figures"].disable()
    _utf8_when_piped()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["hook", "claude"]:        # after every agent edit: skip argparse entirely
        from .edithook import run_edit_hook
        code, text = run_edit_hook(sys.stdin.read() if not sys.stdin.isatty() else "")
        if text:
            sys.stderr.write(text)
        return code
    parser = make_parser()
    cmd: list[str] = []
    if argv[:1] == ["run"] and "--" in argv:         # vouch run ID [options] -- CMD ...
        cut = argv.index("--")
        argv, cmd = argv[:cut], argv[cut + 1:]
    args = parser.parse_args(argv)
    if getattr(args, "command", None) == "run":
        args.cmd = cmd
    if not getattr(args, "fn", None):
        parser.print_help()
        return EXIT_OK
    if getattr(args, "command", None) == "ack" and not (args.keys or args.all or args.run):
        parser.error("ack needs KEY..., --all or --run RUN")
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
