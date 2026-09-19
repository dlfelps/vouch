"""The ``vouch`` command line (SPEC §12)."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import changes as ch
from . import console as C
from .config import Config, ConfigError, discover_root

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

def print_changes(changes: list, *, heading: bool = True) -> None:
    pending = [c for c in changes if c.pending]
    if not pending:
        return
    if heading:
        n = len(pending)
        C.out("")
        C.out(f"{n} CHANGED VALUE{'S' if n > 1 else ''} — re-read the sentences below")
    for c in pending:
        C.out("")
        label = {"suspicious": "SUSPICIOUS", "changed": "CHANGED",
                 "figure-changed": "FIGURE"}[c.cls]
        old = " | ".join(ch.readable(p) for p in (c.old or {}).get("plain", [])) or "?"
        new = " | ".join(ch.readable(p) for p in c.new.plain) or "?"
        extra = "; ".join(x for x in (ch.describe_delta(c), "; ".join(c.reasons)) if x)
        if c.cls == "figure-changed":
            C.out(f"  {label:<10}  {c.key}   the figure file changed")
        else:
            C.out(f"  {label:<10}  {c.key}   {old} → {new}" + (f"   ({extra})" if extra else ""))
        for w in c.citations[:8]:
            C.out(f"    {w.file}:{w.line}  \"{w.sentence}\"")
    C.out("")
    C.out("  → fix any sentence that is now wrong, then: vouch ack <key>…  or  vouch review")


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
    C.out("next: record values in your experiments, cite them as \\vouch{key}, run `vouch build`")
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
        ctx = plan(cfg, check_env=not args.no_env)
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


def cmd_ls(args) -> int:
    from .build import BuildError, plan
    try:
        cfg = _config(args)
        ctx = plan(cfg, check_env=False)
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
    for key in sorted(ctx.idx.entries):
        e = ctx.idx.entries[key]
        if not _match(args.pattern, key):
            continue
        n = counts.get(key, 0)
        if (args.cited and not n) or (args.uncited and n):
            continue
        r = rendered.get((key, ""))
        st = ctx.states.get(e.run or "")
        rows.append({"key": key, "kind": e.kind,
                     "value": ch.readable(r.plain) if r else ("HOLDS" if e.raw else "FALSE")
                     if e.kind == "claim" else "",
                     "desc": e.desc or "", "run": e.run or "", "state": st.state if st else "",
                     "cited": n, "fmt": e.fmt or "", "unit": e.unit or "", "better": e.better or ""})
    if args.json:
        print(_envelope("ls", True, keys=rows))
        return EXIT_OK
    if not rows:
        C.out("no matching keys")
        return EXIT_OK
    w = min(max(len(r["key"]) for r in rows), 40)
    for r in rows:
        desc = r["desc"] if len(r["desc"]) <= 60 else r["desc"][:57] + "..."
        C.out(f"{r['key']:<{w}}  {r['value']:<18}  {r['state']:<8} cited {r['cited']:<3} {desc}")
    return EXIT_OK


def cmd_trace(args) -> int:
    from .build import BuildError, plan
    try:
        cfg = _config(args)
        ctx = plan(cfg, check_env=False)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        return _fatal("trace", exc)
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
    C.err(f"vouch trace: {target!r} is not a key, a tex file:line, or a file any run used"
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
    if e.run:
        rec = idx.runs.get(e.run) or {}
        st = ctx.states.get(e.run)
        out.append(f"  recorded  {e.site or '-'}   in run {e.run}")
        if e.extra.get("call"):
            from .track import call_text, per_call_text
            call = e.extra["call"]
            out.append(f"  by        {call_text(call)}"
                       + (f"   (listed in vouch.toml {call['via']})" if call.get("via") else ""))
            each = per_call_text(call, limit=len(call.get("results") or []))
            if each:
                out.append(f"  each call {each}")
            if call.get("sites"):
                out.append(f"  called at {', '.join(call['sites'][:6])}")
        out.append(f"  command   {' '.join(rec.get('command') or [])}")
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
    where = [f"{c.file}:{c.line}" for pl in ctx.plans for c in pl.doc.citations
             if c.key == key or (c.kind == "table" and e.parent == c.key)]
    out.append(f"  cited     {', '.join(dict.fromkeys(where)) or '(not cited)'}")
    change = ctx.pending.get(key)
    if change:
        out.append(f"  pending   {ch.was_text(change)} -- re-read, then vouch ack {key}")
    for line in out:
        C.out(indent + line)
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
        chosen = [c for c in pending if c.key in wanted]
        unknown = wanted - {c.key for c in chosen}
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
    sp.add_argument("--json", action="store_true")

    sp = add("trace", cmd_trace, "where a value came from (a key, a tex file:line, or a script)")
    sp.add_argument("target")
    sp.add_argument("--code", action="store_true", help="list every code unit")

    sp = add("accept", cmd_accept, "record that a stale run's result still stands")
    sp.add_argument("run")
    sp.add_argument("--why", required=True, help="why the measurement is unaffected")

    sp = add("changes", cmd_changes, "cited values that changed since last acknowledged")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--md", metavar="FILE", help="write a shareable Markdown review report")

    sp = add("ack", cmd_ack, "acknowledge changed values after re-reading their sentences")
    sp.add_argument("keys", nargs="*")
    sp.add_argument("--all", action="store_true", help="every pending change")
    sp.add_argument("--run", help="every pending change from this run")
    sp.add_argument("--why", default="acknowledged", help="recorded with the acknowledgment")

    add("review", cmd_review, "step through pending changes interactively")

    sp = add("hook", cmd_hook, "install the git pre-commit hook")
    sp.add_argument("action", choices=["install"])
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
    from .tracing import tracker
    tracker.stop()                     # the CLI runs no experiment; nothing to track
    _utf8_when_piped()
    parser = make_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return EXIT_OK
    if getattr(args, "command", None) == "ack" and not (args.keys or args.all or args.run):
        parser.error("ack needs KEY..., --all or --run RUN")
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
