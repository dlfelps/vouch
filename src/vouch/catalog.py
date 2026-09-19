"""``.vouch/CATALOG.md``: everything citable, in one file an agent can read (SPEC §13.1).

Written by every ``vouch build`` (and ``vouch catalog``). One line per key, fields
separated by ``·`` and left out when empty, grouped by the key's first segment,
then claims, tables, aliases, what is pending, and what changed since it was last
acknowledged. Subfields (``.mean``, ``.std``, ...) and table cells are described
once in the header rather than listed.

Projects with more than 300 keys get an index here and one file per prefix under
``.vouch/catalog/``.
"""

from __future__ import annotations

from pathlib import Path

from .changes import readable
from .index import source_runs
from .render import Options, RenderError, render

SPLIT_AT = 300
HEADER = [
    "Cite: \\vouch{key} · \\vouch[fmt]{key} · \\vouchclaim{key}{text} · \\vouchtable{key}",
    "Formats: .1f .2e ,d .1pct (x = \\times, u = unit) · a Stat renders mean \\pm std · "
    "subfields .mean .std .n .ci95 .min .max · table cells <table>.<row>.<column>",
    "Find: vouch search \"words\" · Snippet: vouch cite KEY · Arithmetic and claims: "
    "vouch compare A B · Missing: vouch.expect(...) · Browse: vouch explore",
    "Never type a number. Never compute with numbers in prose.",
]
_ORDER = ("tampered", "incomplete", "stale", "upstream-stale", "accepted", "cosmetic", "fresh")


def _shown(e, opts, rendered) -> str:
    r = rendered.get((e.key, ""))
    if r is None:
        try:
            r = render(e.raw, e.fmt, unit=e.unit, opts=opts)
        except (RenderError, ValueError, TypeError):
            return str(e.raw)
    return readable(r.plain).replace("+/-", "±")


def _state(ctx, e) -> str:
    states = [ctx.states[r].state for r in source_runs(e) if r in ctx.states]
    if not states:
        return ""
    worst = min(states, key=lambda s: _ORDER.index(s) if s in _ORDER else len(_ORDER))
    return "fresh" if worst == "cosmetic" else worst.upper() if worst in _ORDER[:4] else worst


def _cites(ctx) -> dict[str, int]:
    n: dict[str, int] = {}
    for pl in ctx.plans:
        for c in pl.doc.citations:
            n[c.key] = n.get(c.key, 0) + 1
    return n


def lines_for(ctx) -> dict[str, list[str]]:
    """{section: lines}: values by prefix, then claims, tables, aliases, pending, changed."""
    idx = ctx.idx
    opts = Options.from_config(ctx.cfg)
    rendered = ctx.plans[0].rendered if ctx.plans else {}
    cites = _cites(ctx)
    changes = ctx.pending                  # changed since last acknowledged
    out: dict[str, list[str]] = {}
    aliases: dict[str, str] = {}
    for key in sorted(idx.entries):
        e = idx.entries[key]
        if e.kind in ("stat-field", "element", "table-cell", "table"):
            continue
        if e.extra.get("alias_of"):
            short, full = key, e.extra["alias_of"]
            while "." in short and "." in full and short.rsplit(".", 1)[1] == full.rsplit(".", 1)[1]:
                short, full = short.rsplit(".", 1)[0], full.rsplit(".", 1)[0]
            aliases.setdefault(short, full)
            continue
        state = _state(ctx, e)
        if key in changes:
            state = "CHANGED"
        n = cites.get(key, 0)
        if e.kind == "claim":
            expl = e.extra.get("explanation")
            m = e.extra.get("margin")
            holds = ("HOLDS" if e.raw else "FALSE") + (
                f" ({expl}" + (f", margin {m:.1%}" if isinstance(m, (int, float)) else "") + ")"
                if expl else "")
            fields = [key, holds, e.desc or "", state, f"cited {n}×" if n else ""]
            out.setdefault("claims", []).append(" · ".join(f for f in fields if f))
            continue
        origin = "derived" if e.extra.get("derived") else (e.run or "")
        better = {"higher": "higher↑", "lower": "lower↓"}.get(e.better or "", "")
        tags = "timing" if e.extra.get("timing") else ""
        fields = [key, _shown(e, opts, rendered), e.desc or "", e.unit or "", better, tags, origin,
                  state, f"cited {n}×" if n else ""]
        section = key.split(".", 1)[0]
        out.setdefault(section, []).append(" · ".join(f for f in fields if f))
    for key in sorted(idx.tables):
        t = idx.tables[key]
        if t.alias_of:
            continue
        fields = [key, f"{len(t.rows)}×{len(t.columns)}", " | ".join(t.columns), t.desc or "",
                  f"cells {key}.<{t.row_key or 'row'}>.<column>",
                  "derived" if t.derived else (t.run or ""),
                  f"cited {cites.get(key, 0)}×" if cites.get(key) else ""]
        out.setdefault("tables", []).append(" · ".join(f for f in fields if f))
    for short, full in sorted(aliases.items()):
        out.setdefault("aliases", []).append(f"{short}.* → {full}.*")
    for key, p in sorted(idx.pending.items()):
        producer = p.get("producer")
        waits = p.get("waits")
        fields = [key, p.get("desc") or (f"computed from {', '.join(waits)}" if waits else ""),
                  f"run: {producer}" if producer else "", f"cited {cites.get(key, 0)}×"
                  if cites.get(key) else ""]
        out.setdefault("pending (vouch todo)", []).append(" · ".join(f for f in fields if f))
    for key, c in sorted(changes.items()):
        old = " | ".join(readable(x) for x in (c.old or {}).get("plain", [])) or "?"
        new = " | ".join(readable(x) for x in c.new.plain) or "?"
        where = ", ".join(f"{w.file}:{w.line}" for w in c.citations[:4])
        cls = c.cls.upper() + (f" ({'; '.join(c.reasons)})" if c.reasons else "")
        out.setdefault("changed since last ack (vouch changes)", []).append(
            " · ".join(x for x in (key, f"{old} → {new}", cls, where) if x))
    return out


def _counts(ctx, sections: dict[str, list[str]]) -> str:
    idx = ctx.idx
    n_values = sum(len(v) for k, v in sections.items()
                   if k not in ("claims", "tables", "aliases") and not k.startswith(("pending", "changed")))
    parts = [f"{n_values} values", f"{len(sections.get('claims', []))} claims",
             f"{len(idx.tables)} tables"]
    if idx.pending:
        parts.append(f"{len(idx.pending)} pending")
    if ctx.pending:
        parts.append(f"{len(ctx.pending)} changed")
    return " · ".join(parts)


_SPECIAL = ("claims", "tables", "aliases", "pending (vouch todo)",
            "changed since last ack (vouch changes)")


def render_catalog(ctx) -> dict[Path, str]:
    """{path: content} for the catalog (and, for big projects, its per-prefix files)."""
    sections = lines_for(ctx)
    store = ctx.cfg.store
    head = [f"# vouch catalog — {_counts(ctx, sections)}"] + HEADER + [""]
    prefixes = [k for k in sorted(sections) if k not in _SPECIAL]
    total = sum(len(v) for v in sections.values())
    files: dict[Path, str] = {}
    body: list[str] = []
    if total <= SPLIT_AT:
        for k in prefixes:
            body += [f"## {k} ({len(sections[k])})"] + sections[k] + [""]
    else:
        body.append("## index (one file per prefix under .vouch/catalog/)")
        for k in prefixes:
            body.append(f"{k} · {len(sections[k])} keys · .vouch/catalog/{k}.md")
            files[store / "catalog" / f"{k}.md"] = "\n".join(
                [f"# vouch catalog: {k} ({len(sections[k])})", ""] + sections[k]) + "\n"
        body.append("")
    for k in _SPECIAL:
        if sections.get(k):
            body += [f"## {k}"] + sections[k] + [""]
    files[store / "CATALOG.md"] = "\n".join(head + body).rstrip("\n") + "\n"
    return files


def write_catalog(ctx) -> list[Path]:
    written = []
    files = render_catalog(ctx)
    old_split = ctx.cfg.store / "catalog"
    if old_split.is_dir():                      # prefixes that no longer exist
        for p in old_split.glob("*.md"):
            if p not in files:
                p.unlink()
    for path, text in files.items():
        try:
            if path.read_text(encoding="utf-8") == text:
                continue
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
