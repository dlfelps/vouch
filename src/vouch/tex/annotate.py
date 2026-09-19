"""Source annotations: the numbers, visible in the ``.tex`` (SPEC §7.8, opt-in).

``\\vouch{key}`` keeps the source free of typed numbers, but hides them from someone
reading it. With ``[latex] annotate = true``, ``vouch build`` (or ``vouch sync``)
keeps a managed trailing comment on every line that cites values::

    ResNet-50 reaches \\vouch{cifar.resnet.acc} top-1 accuracy.  % vouch: cifar.resnet.acc=93.2±0.4%

vouch owns only that suffix: a comment starting ``% vouch: key=``. It is plain
text, never read back (comments are invisible to the scanner), and ``vouch sync
--strip`` removes every one. The lint's ``% vouch: ignore`` pragma is left alone.
"""

from __future__ import annotations

import re
from pathlib import Path

MARK = re.compile(r"[ \t]*% vouch: (?=[A-Za-z0-9_.\-]+=).*$")


def notes_for(pl, idx) -> dict[str, dict[int, str]]:
    """{file: {line: "key=value, key=value"}} for every line that cites values."""
    from ..changes import readable
    out: dict[str, dict[int, list[str]]] = {}
    for c in pl.doc.citations:
        if c.kind not in ("value", "raw", "claim"):
            continue
        e = idx.get(c.key)
        if e is None:
            continue
        if c.kind == "claim":
            shown = "HOLDS" if e.raw else "FALSE"
        else:
            r = pl.rendered.get((c.key, (c.fmt or "") if c.kind == "value" else ""))
            if r is None:
                continue
            shown = readable(r.plain).replace("+/-", "±")
        item = f"{c.key}={shown}"
        got = out.setdefault(c.file, {}).setdefault(c.line, [])
        if item not in got:
            got.append(item)
    return {f: {ln: ", ".join(items) for ln, items in lines.items()} for f, lines in out.items()}


def rewrite(text: str, notes: dict[int, str], strip: bool = False) -> str:
    """``text`` with its managed suffixes replaced by ``notes`` (or removed)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    for i, line in enumerate(lines):
        bare = MARK.sub("", line)
        note = None if strip else notes.get(i + 1)
        lines[i] = f"{bare}  % vouch: {note}" if note else bare
    return nl.join(lines)


def annotate(ctx, *, strip: bool = False) -> list[Path]:
    """Refresh (or strip) the annotations in every paper's tex files; the files changed."""
    root = ctx.cfg.root
    changed: list[Path] = []
    for pl in ctx.plans:
        notes = {} if strip else notes_for(pl, ctx.idx)
        for path in pl.doc.files:
            try:
                path.resolve().relative_to(root)
            except ValueError:
                continue                          # never touch files outside the project
            rel = pl.doc.rel(path)
            with open(path, encoding="utf-8", newline="") as fh:
                text = fh.read()
            new = rewrite(text, notes.get(rel, {}), strip=strip)
            if new != text:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
                changed.append(path)
    return changed
