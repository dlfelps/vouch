"""``vouch document``: a real-values summary of a script.

For one script, or every script that has recorded a run, assembles its module
and function docstrings -- read fresh from the current source, verbatim --
next to the actual current value of every key its functions produced, with
freshness and run metadata. Nothing here is invented: every word is the
user's own docstring, and every number already passed through a run vouch
verified.

Meant for sharing a script with someone, or coming back to it months later --
it needs no ``[[paper]]`` in ``vouch.toml``, and no citation anywhere.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from . import docextract
from .changes import readable
from .index import source_runs
from .render import Options, RenderError, render

MODULE = docextract.MODULE
_ORDER = ("tampered", "incomplete", "stale", "upstream-stale", "accepted", "cosmetic", "fresh")


@dataclasses.dataclass
class KeyLine:
    key: str
    value: str
    desc: str
    state: str
    cited: int


@dataclasses.dataclass
class FunctionSection:
    qualname: str
    context: str | None
    keys: list[KeyLine]


@dataclasses.dataclass
class ScriptSummary:
    script: str
    context: str | None
    last_run: str | None
    commit: str | None
    dirty: bool
    command: str | None
    state: str
    functions: list[FunctionSection]


def _resolve_script(cfg, target: str) -> str | None:
    """A project-relative path for ``target`` -- the same resolution ``vouch
    trace`` uses for a script argument -- or ``None`` if nothing matches."""
    cands = {cfg.rel(target), Path(target).as_posix().removeprefix("./")}
    return next((c for c in cands if (cfg.root / c).exists()), None)


def _shown(e, opts: Options) -> str:
    try:
        r = render(e.raw, e.fmt, unit=e.unit, opts=opts)
    except (RenderError, ValueError, TypeError):
        return str(e.raw)
    return readable(r.plain).replace("+/-", "±")


def _cites(ctx) -> dict[str, int]:
    n: dict[str, int] = {}
    for pl in ctx.plans:
        for c in pl.doc.citations:
            n[c.key] = n.get(c.key, 0) + 1
    return n


def _worst(states: list[str]) -> str:
    found = [s for s in states if s]
    if not found:
        return ""
    return min(found, key=lambda s: _ORDER.index(s) if s in _ORDER else len(_ORDER))


def _scripts_with_runs(ctx) -> list[str]:
    return sorted({rec.get("entry") for rec in ctx.idx.runs.values() if rec.get("entry")})


def summarize_one(ctx, rel: str) -> ScriptSummary | None:
    """The summary for the script at project-relative path ``rel`` -- ``None``
    if no run used it."""
    idx = ctx.idx
    runs = [r for r, rec in sorted(idx.runs.items())
            if rec.get("entry") == rel or any(u.split("::")[0] == rel
                                              for u in (rec.get("code") or {}).get("units", {}))]
    if not runs:
        return None
    try:
        source = (ctx.cfg.root / rel).read_text(encoding="utf-8")
    except OSError:
        docs: dict[str, str] = {}
    else:
        docs = docextract.extract(source, rel)
    opts = Options.from_config(ctx.cfg)
    cites = _cites(ctx)
    order: list[str] = []
    buckets: dict[str, list[KeyLine]] = {}
    for key in sorted(k for k, e in idx.entries.items() if e.run in runs and e.kind == "value"):
        e = idx.entries[key]
        call = e.extra.get("call") or {}
        fn_ref = call.get("function")
        qual = fn_ref.split("::", 1)[1] if fn_ref else MODULE
        if qual not in buckets:
            buckets[qual] = []
            order.append(qual)
        state = _worst([ctx.states[r].state for r in source_runs(e) if r in ctx.states])
        buckets[qual].append(KeyLine(key=key, value=_shown(e, opts), desc=e.desc or "",
                                     state=state, cited=cites.get(key, 0)))
    functions = [FunctionSection(qualname=q, context=docs.get(q), keys=buckets[q]) for q in order]
    last, commit, dirty, command = None, None, False, None
    for r in runs:
        rec = idx.runs[r]
        started = rec.get("started")
        if started and (last is None or started > last):
            last = started
            git = rec.get("git") or {}
            commit, dirty = git.get("commit"), bool(git.get("dirty"))
            command = " ".join(rec.get("command") or []) or None
    state = _worst([ctx.states[r].state for r in runs if r in ctx.states])
    return ScriptSummary(script=rel, context=docs.get(MODULE), last_run=last, commit=commit,
                         dirty=dirty, command=command, state=state, functions=functions)


def summarize(ctx, script: str | None) -> list[ScriptSummary]:
    """One summary per script: the one named, or every script with a recorded
    run, if ``script`` is ``None``."""
    if script is not None:
        rel = _resolve_script(ctx.cfg, script)
        if rel is None:
            return []
        one = summarize_one(ctx, rel)
        return [one] if one else []
    out = []
    for rel in _scripts_with_runs(ctx):
        one = summarize_one(ctx, rel)
        if one is not None:
            out.append(one)
    return out


def _meta_line(s: ScriptSummary) -> str:
    meta = []
    if s.last_run:
        meta.append(f"last run {str(s.last_run).replace('T', ' ')[:16]} UTC")
    if s.commit:
        meta.append(f"git {s.commit[:7]}" + (" (dirty)" if s.dirty else ""))
    if s.state:
        meta.append(s.state)
    return " · ".join(meta)


def render_text(summaries: list[ScriptSummary]) -> list[str]:
    lines: list[str] = []
    for s in summaries:
        lines.append(f"## {s.script}")
        if s.context:
            lines.append(s.context)
        meta = _meta_line(s)
        if meta:
            lines.append(meta)
        for fn in s.functions:
            heading = fn.qualname if fn.qualname != MODULE else "(top level)"
            lines.append("")
            lines.append(f"### {heading}")
            if fn.context and fn.qualname != MODULE:
                lines.append(fn.context)
            w = max((len(k.key) for k in fn.keys), default=0)
            v = max((len(k.value) for k in fn.keys), default=0)
            for k in fn.keys:
                tail = f"cited {k.cited}×" if k.cited else "not cited"
                if k.state and k.state != "fresh":
                    tail += f"   {k.state}"
                lines.append(f"  {k.key:<{w}}  {k.value:<{v}}  {k.desc[:50]:<50}  {tail}")
        lines.append("")
    return lines


def render_markdown(summaries: list[ScriptSummary]) -> str:
    out = ["# Script documentation", ""]
    for s in summaries:
        out.append(f"## `{s.script}`")
        out.append("")
        if s.context:
            out.append(f"*{s.context}*")
            out.append("")
        meta = _meta_line(s)
        if s.command:
            meta = (meta + " · " if meta else "") + f"`{s.command}`"
        if meta:
            out.append(meta)
            out.append("")
        for fn in s.functions:
            heading = fn.qualname if fn.qualname != MODULE else "top level"
            out.append(f"### `{heading}`")
            out.append("")
            if fn.context and fn.qualname != MODULE:
                out.append(f"*{fn.context}*")
                out.append("")
            out.append("| key | value | description | state | cited |")
            out.append("|---|---|---|---|---|")
            for k in fn.keys:
                out.append(f"| `{k.key}` | {k.value} | {k.desc} | {k.state or 'fresh'} | "
                           f"{k.cited if k.cited else '-'} |")
            out.append("")
    return "\n".join(out) + "\n"


def to_json(summaries: list[ScriptSummary]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(s) for s in summaries]
