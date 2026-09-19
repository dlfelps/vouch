"""The Claude Code edit hook: check a ``.tex`` file the moment an agent edits it (SPEC §13.5).

Claude Code runs ``vouch hook claude`` after every ``Edit``/``Write``/``MultiEdit``,
with the tool call as JSON on stdin. For a ``.tex`` file in a vouch project, the
edited file (only) is checked for:

* keys no run, definition or expectation provides (with did-you-mean);
* malformed vouch macros (``\\vouch{`` never closed, ``\\vouchclaim`` without prose);
* numbers typed by hand -- ``bare-number`` with the exact ``\\vouch`` to paste, or
  ``no-source`` when nothing any run produced prints like that.

Problems go to stderr and the hook exits 2, which Claude Code feeds back to the
agent in the same turn: a slip is corrected immediately, not at commit time.

It must stay fast (it runs after every edit), so it reads the small index ``vouch
build`` writes to ``.vouch/cache/index.json`` instead of loading the run store, and
does nothing at all for files outside a vouch project.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from .config import Config, ConfigError, discover_root

INDEX = "cache/index.json"
_MACRO = re.compile(r"\\(vouchclaim|vouchtable|vouchraw|vouch)(?![A-Za-z@])")


def write_index(ctx) -> Path | None:
    """What the hook needs, precomputed by ``vouch build``."""
    from .tex.lint import Candidates
    from .valuesmod import static_keys, values_modules
    idx = ctx.idx
    cands = Candidates(idx)
    data = {
        "schema": "vouch/1",
        "keys": sorted(set(idx.entries) | set(idx.tables)),
        "pending": {k: d.get("producer") for k, d in sorted(idx.pending.items())},
        "defined": sorted(static_keys(values_modules(ctx.cfg))),
        "numbers": [[v, r, k] for v, r, k in cands.rows],
        "stats": [[k, s.mean, s.std] for k, s in cands.stats],
        "papers": [{"main": ctx.cfg.rel(pl.main), "short": pl.doc.short,
                    "files": sorted(pl.doc.rel(p) for p in pl.doc.files)} for pl in ctx.plans],
    }
    path = ctx.cfg.store / INDEX
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    try:
        if path.read_text(encoding="utf-8") == text:
            return None
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class _Cached:
    """The lint's candidate index, rebuilt from the cache instead of the run store."""

    def __init__(self, data: dict):
        from .values import Stat
        rows = sorted((float(v), int(r), str(k)) for v, r, k in data.get("numbers", []))
        self.rows = rows
        self.values = [r[0] for r in rows]
        self.stats = [(k, Stat(m, s, 2)) for k, m, s in data.get("stats", [])]

    def near(self, x: float, tol: float):
        import bisect
        lo = bisect.bisect_left(self.values, x - tol)
        hi = bisect.bisect_right(self.values, x + tol)
        return self.rows[lo:hi]


def _malformed(masked: str) -> list[tuple[int, str]]:
    from .tex.scan import balanced, line_of
    out = []
    for m in _MACRO.finditer(masked):
        name, i = m.group(1), m.end()
        while i < len(masked) and masked[i] in " \t\n":
            i += 1
        if name == "vouch" and i < len(masked) and masked[i] == "[":
            got = balanced(masked, i, "[", "]")
            if got is None:
                out.append((line_of(masked, m.start()), f"\\vouch[ has no closing ]"))
                continue
            i = got[1]
            while i < len(masked) and masked[i] in " \t\n":
                i += 1
        if i >= len(masked) or masked[i] != "{":
            out.append((line_of(masked, m.start()), f"\\{name} needs its key in braces: \\{name}{{key}}"))
            continue
        got = balanced(masked, i)
        if got is None:
            out.append((line_of(masked, m.start()), f"\\{name}{{ is never closed"))
            continue
        if name == "vouchclaim":
            j = got[1]
            while j < len(masked) and masked[j] in " \t\n":
                j += 1
            if j >= len(masked) or masked[j] != "{" or balanced(masked, j) is None:
                out.append((line_of(masked, m.start()),
                            "\\vouchclaim takes two arguments: \\vouchclaim{key}{the prose it vouches for}"))
    return out


def check_file(path: Path) -> list[str] | None:
    """Problems in one edited tex file, as lines for the agent; None if it isn't ours."""
    from .tex.lint import _allowed, find_literals, match, suggestion
    from .tex.scan import Document, _Scanner
    path = path.resolve()
    root, how = discover_root(path.parent)
    if how not in ("config", "env"):
        return None
    try:
        cfg = Config.load(root)
    except ConfigError:
        return None
    try:
        data = json.loads((cfg.store / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                               # never built: `vouch build` reports everything
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return None
    papers = data.get("papers") or []
    paper = next((p for p in papers if rel in p.get("files", [])), None)
    if paper is None:
        dirs = [str(Path(p["main"]).parent.as_posix()) for p in papers]
        if not any(rel.startswith(d + "/") or d in ("", ".") for d in dirs):
            return None
    s = _Scanner(path, root, cfg.get("lint", "skip_envs", ()))
    masked = s.load(path)
    if masked is None:
        return None
    s.short = bool(paper and paper.get("short"))
    m = re.search(r"\\begin\s*\{document\}", masked)
    body = m.end() if m else 0
    problems: list[tuple[int, str]] = list(_malformed(masked))

    keys = set(data.get("keys", []))
    pending = data.get("pending") or {}
    defined = set(data.get("defined", []))
    from .valuesmod import static_keys, values_modules
    defined |= static_keys(values_modules(cfg))           # defined since the last build

    def known(k: str) -> bool:
        return k in keys or any(k == d or k.startswith(d + ".") for d in list(pending) + list(defined))
    for _, c in s._cites_in(masked, body, len(masked), path):
        if c.kind == "figure" or known(c.key):
            continue
        sugg = difflib.get_close_matches(c.key, list(keys), n=3, cutoff=0.6)
        problems.append((c.line, f"unknown key {c.key}: no run, definition or vouch.expect "
                                 f"provides it" + (f" (did you mean {', '.join(sugg)}?)" if sugg
                                                   else "; find it with `vouch search`")))

    level = str(cfg.get("lint", "level", "warn") or "warn")
    if level != "off":
        doc = Document(main=path, root=root, files=[path], citations=[], short=s.short,
                       body_start={path: body}, missing_inputs=[], raw=dict(s.raw),
                       masked=dict(s.masked))
        rules = [r for r in (cfg.get("lint", "allow", []) or []) if isinstance(r, dict)]
        lines = s.raw[path].split("\n")
        cands = _Cached(data)
        seen = set()
        for lit in find_literals(doc, allow_years=bool(cfg.get("lint", "allow_years", True))):
            if (lit.line, lit.text) in seen:
                continue
            seen.add((lit.line, lit.text))
            raw_line = lines[lit.line - 1] if 0 < lit.line <= len(lines) else ""
            if _allowed(lit, rules, raw_line):
                continue
            exact, _near = match(lit, cands)
            shown = lit.text.replace("\\%", "%").replace("{,}", ",")
            if exact:
                problems.append((lit.line, f"{shown} is typed by hand; it is {exact[0].key}: "
                                           f"replace it with {suggestion(lit, exact[0])}"))
            else:
                problems.append((lit.line, f"{shown} matches no recorded value: don't type "
                                           f"numbers -- record it in the experiment, or "
                                           f"vouch.expect() it and tell the user it is owed"))
    if not problems:
        return []
    out = [f"vouch: {rel} has {len(problems)} problem(s) -- fix them now:"]
    for line, msg in sorted(problems):
        out.append(f"  line {line}: {msg}")
    return out


def run_edit_hook(stdin_text: str) -> tuple[int, str]:
    """(exit code, stderr text) for one PostToolUse payload."""
    try:
        payload = json.loads(stdin_text or "{}")
    except ValueError:
        return 0, ""
    ti = payload.get("tool_input") or {}
    file = ti.get("file_path") or ti.get("path") or ""
    if not str(file).endswith(".tex"):
        return 0, ""
    try:
        got = check_file(Path(file))
    except Exception as exc:              # a hook must never get in the agent's way
        return 0, f"vouch hook: {exc!r}"
    if not got:
        return 0, ""
    return 2, "\n".join(got) + "\n"


def run_stop_hook(stdin_text: str, root: Path | None) -> tuple[int, str]:
    """``vouch check --strict`` before an agent finishes; exit 2 (with the issues) blocks."""
    try:
        payload = json.loads(stdin_text or "{}")
    except ValueError:
        payload = {}
    if payload.get("stop_hook_active"):
        return 0, ""                          # already blocked once: don't loop
    from .check import run_check
    try:
        found_root, how = discover_root(root or Path.cwd())
        if how not in ("config", "env"):
            return 0, ""
        rep = run_check(Config.load(found_root), strict=True, check_env=False)
    except Exception as exc:
        return 0, f"vouch hook: {exc!r}"
    if rep.ok:
        return 0, ""
    lines = [f"vouch check --strict fails ({len(rep.errors)} problem(s)); fix these before finishing:"]
    for i in rep.errors[:12]:
        where = f" ({i.file}:{i.line})" if i.file and i.line else ""
        lines.append(f"  {i.check}: {i.message}{where}" + (f" -- fix: {i.fix}" if i.fix else ""))
    return 2, "\n".join(lines) + "\n"
