"""The ``vouch`` command line (SPEC §12)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import console as C
from .config import Config, ConfigError, discover_root

EXIT_OK, EXIT_FAIL, EXIT_UNVERIFIED = 0, 1, 2


def _root(args) -> Path:
    if args.root:
        return Path(args.root).resolve()
    root, _ = discover_root(Path.cwd())
    return root


def _config(args) -> Config:
    return Config.load(_root(args))


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def _print_issues(issues, cfg: Config) -> None:
    for i in issues:
        sym = {"error": "✗", "warning": "!", "info": "·"}[i.severity]
        where = f"  ({i.where()})" if i.where() else ""
        C.out(f"  {sym} {i.check:<14} {i.message}{where}")
        if i.fix and i.severity == "error":
            C.out(f"    {'':<14} fix: {i.fix}")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    from .init import InitError, init
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    try:
        notes = init(root, args.paper)
    except (InitError, ConfigError) as exc:
        C.err(f"vouch init: {exc}")
        return EXIT_UNVERIFIED
    for n in notes:
        C.out(f"  {n}")
    C.out("next: record values in your experiments, cite them as \\vouch{key}, run `vouch build`")
    return EXIT_OK


def cmd_build(args) -> int:
    from .build import BuildError, build
    try:
        cfg = _config(args)
        res = build(cfg)
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        C.err(f"vouch build: {exc}")
        return EXIT_UNVERIFIED
    if args.json:
        print(json.dumps(_build_json(res, cfg), indent=2))
        return EXIT_OK
    written = {cfg.rel(p) for p in res.written}
    for pl in res.plans:
        C.out(f"vouch build: {cfg.rel(pl.main)}")
        files = [cfg.rel(pl.values_path), cfg.rel(pl.csv_path)]
        wrote = [f for f in files if f in written]
        tables_written = [p for p in res.written if pl.tables_dir in p.parents]
        c = pl.counts
        C.out(f"  {'wrote' if wrote or tables_written else 'unchanged'}: {cfg.rel(pl.values_path)} "
              f"({c['values']} values, {c['claims']} claims, {c['tables']} tables; "
              f"{c['citations']} citations), {cfg.rel(pl.csv_path)}"
              + (f", {len(tables_written)} table file(s)" if tables_written else ""))
        _print_issues(pl.issues, cfg)
    _print_issues(res.issues, cfg)
    return EXIT_OK


def _build_json(res, cfg: Config) -> dict:
    def issue(i):
        return {"check": i.check, "severity": i.severity, "message": i.message,
                "where": i.where() or None, "fix": i.fix}
    return {"schema": "vouch/1", "command": "build", "ok": True,
            "papers": [{"main": cfg.rel(pl.main), "values_file": cfg.rel(pl.values_path),
                        "provenance_csv": cfg.rel(pl.csv_path), "counts": pl.counts,
                        "issues": [issue(i) for i in pl.issues]} for pl in res.plans],
            "issues": [issue(i) for i in res.issues],
            "written": sorted(cfg.rel(p) for p in res.written)}


def cmd_export(args) -> int:
    from .build import BuildError, paper_paths, papers, plan_paper
    from .index import Index
    from .provenance import csv_text
    try:
        cfg = _config(args)
        idx = Index.load(cfg)
        chosen = papers(cfg)
        if args.paper:
            chosen = [p for p in chosen if p["main"] == args.paper] or [{"main": args.paper}]
        pl = plan_paper(cfg, idx, chosen[0])
    except (BuildError, ConfigError, FileNotFoundError) as exc:
        C.err(f"vouch export: {exc}")
        return EXIT_UNVERIFIED
    if args.json:
        from .provenance import rows
        print(json.dumps({"schema": "vouch/1", "command": "export",
                          "rows": rows(pl.doc, idx, pl.rendered, args.all)}, indent=2))
        return EXIT_OK
    text = csv_text(pl.doc, idx, pl.rendered, cfg, include_uncited=args.all)
    if args.csv in (None, "-"):
        sys.stdout.write(text)
    else:
        Path(args.csv).write_text(text, encoding="utf-8", newline="\n")
        C.out(f"wrote {args.csv} ({text.count(chr(10)) - 1} rows)")
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

    sp = add("build", cmd_build, "render values, tables and the provenance CSV for the paper")
    sp.add_argument("--json", action="store_true", help="machine-readable summary")

    sp = add("export", cmd_export, "write the provenance table")
    sp.add_argument("--csv", help="output path (default: stdout)")
    sp.add_argument("--all", action="store_true", help="include recorded keys the paper doesn't cite")
    sp.add_argument("--paper", help="which paper (main .tex), if there are several")
    sp.add_argument("--json", action="store_true", help="rows as JSON instead of CSV")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return EXIT_OK
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
