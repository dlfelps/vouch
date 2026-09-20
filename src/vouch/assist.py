"""Lookup and insertion for people and agents: search, cite, compare, todo (SPEC §13.2).

The rule behind all four: wherever an agent would otherwise guess -- a key, a
format, a difference, whether "outperforms" is true -- give it the exact answer
more cheaply than the guess.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from . import docextract
from .changes import readable
from .index import Entry, source_runs
from .render import Options, RenderError, render, tex_escape
from .values import STAT_FIELDS, Stat

_ORDER = ("tampered", "incomplete", "stale", "upstream-stale", "accepted", "cosmetic", "fresh")


def shown(e: Entry, opts: Options, fmt: str | None = None) -> str:
    """How a value prints, as a person reads it: ``93.2 ± 0.4%``."""
    if e.kind == "claim":
        return "HOLDS" if e.raw else "FALSE"
    if e.extra.get("timing") and fmt is None and not e.key.endswith(".n"):
        from .explore import _duration
        got = _duration(e.raw)
        if got:
            return got
    try:
        r = render(e.raw, fmt if fmt is not None else e.fmt, unit=e.unit, opts=opts)
    except (RenderError, ValueError, TypeError):
        return str(e.raw)
    return readable(r.plain).replace("+/-", "±")


def state_of(ctx, e: Entry) -> str:
    found = [ctx.states[r].state for r in source_runs(e) if r in ctx.states]
    if not found:
        return ""
    return min(found, key=lambda s: _ORDER.index(s) if s in _ORDER else len(_ORDER))


def origin_of(e: Entry) -> str:
    if e.extra.get("derived"):
        return "derived"
    return e.run or ""


def context_of(ctx, e: Entry, cache: dict[str, dict[str, str]] | None = None) -> str | None:
    """The producing function's own docstring, read fresh from its current source --
    a sanity check that a key means what its name suggests, not a description vouch
    made up. ``None`` when the value has no producing call, or that function has no
    docstring."""
    call = e.extra.get("call") or {}
    fn_ref = call.get("function")
    if not fn_ref:
        return None
    return docextract.context_for(fn_ref, ctx.cfg.root, cache)


# ---------------------------------------------------------------------------
# search: BM25 over one small document per key
# ---------------------------------------------------------------------------

_SYNONYMS = [
    {"acc", "accuracy", "top1"}, {"lr", "learning", "rate"}, {"std", "deviation", "stdev", "sd"},
    {"n", "count", "seed", "sample", "size"}, {"err", "error"},
    {"time", "second", "runtime", "duration", "wall", "timing", "took", "take", "long", "slow"},
    {"param", "parameter", "hyperparameter", "setting"}, {"pt", "point"},
    {"diff", "difference", "gap", "delta"}, {"ci95", "confidence", "interval", "ci"},
    {"mean", "average", "avg"}, {"f1", "fscore"}, {"auc", "roc"}, {"loss", "cost"},
    {"throughput", "speed", "fps"},
]
_SUB = {"mean", "std", "n", "ci95", "min", "max", "deviation", "stdev", "average", "avg",
        "count", "seed", "confidence", "interval", "minimum", "maximum"}


def tokens(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text))
    out = []
    for t in re.split(r"[^A-Za-z0-9]+", text.lower()):
        if not t:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def _doc(e: Entry, idx) -> list[str]:
    toks = tokens(e.key) * 2 + tokens(e.desc or "") + tokens(e.unit or "")
    toks += tokens(origin_of(e))
    call = e.extra.get("call") or {}
    if call:
        toks += tokens(str(call.get("function", "")).split("::")[-1])
        toks += [t for v in (call.get("args") or {}).values() for t in tokens(v)]
    if e.kind == "table" and e.key in idx.tables:
        toks += [t for c in idx.tables[e.key].columns for t in tokens(c)]
    if e.extra.get("timing"):
        toks += ["time", "timing"]
    return toks


def search(ctx, query: str, limit: int = 10) -> list[dict]:
    """Keys ranked for ``query`` (BM25, with a small synonym list)."""
    idx = ctx.idx
    q = tokens(query)
    if not q:
        return []
    weights: dict[str, float] = {}
    for t in q:
        weights[t] = max(weights.get(t, 0), 1.0)
        for group in _SYNONYMS:
            if t in group:
                for s in group:
                    weights.setdefault(s, 0.5)
    want_sub = any(t in _SUB for t in q)
    want_time = any(t in _SYNONYMS[5] for t in q)
    docs: list[tuple[str, list[str], Entry | None]] = []
    for key, e in idx.entries.items():
        if e.kind == "table-cell" or e.extra.get("alias_of"):
            continue
        if e.kind in ("stat-field", "element") and not want_sub:
            continue
        docs.append((key, _doc(e, idx), e))
    for key, p in idx.pending.items():
        docs.append((key, tokens(key) * 2 + tokens(p.get("desc") or "") + ["pending"], None))
    if not docs:
        return []
    n = len(docs)
    avg = sum(len(d) for _, d, _ in docs) / n
    df: dict[str, int] = {}
    for _, d, _ in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    k1, b = 1.2, 0.75
    scored = []
    for key, d, e in docs:
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t, w in weights.items():
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += w * idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        if e is not None and e.extra.get("timing") and not want_time:
            score *= 0.5                         # durations, unless the query is about time
        if score > 0:
            scored.append((score, key, e))
    scored.sort(key=lambda s: (-s[0], len(s[1]), s[1]))
    opts = Options.from_config(ctx.cfg)
    doc_cache: dict[str, dict[str, str]] = {}
    out = []
    for score, key, e in scored[:limit]:
        if e is None:
            p = idx.pending[key]
            out.append({"key": key, "score": round(score, 3), "kind": "pending", "value": "pending",
                        "desc": p.get("desc") or "", "origin": "vouch.expect", "state": "pending",
                        "cite": "\\vouch{" + key + "}"})
            continue
        hit = {"key": key, "score": round(score, 3), "kind": e.kind,
               "value": "" if e.kind == "table" else shown(e, opts), "desc": e.desc or "",
               "origin": origin_of(e), "state": state_of(ctx, e), "cite": snippet(e)}
        context = context_of(ctx, e, doc_cache)
        if context:
            hit["context"] = context
        out.append(hit)
    return out


def snippet(e: Entry, fmt: str | None = None) -> str:
    if e.kind == "claim":
        return "\\vouchclaim{" + e.key + "}{" + tex_escape(e.desc or "...") + "}"
    if e.kind == "table":
        return "\\vouchtable{" + e.key + "}"
    return "\\vouch" + (f"[{fmt}]" if fmt else "") + "{" + e.key + "}"


# ---------------------------------------------------------------------------
# cite: the snippet, and enough to write the sentence right
# ---------------------------------------------------------------------------

def _finer(fmt: str | None, raw: Any) -> str | None:
    """The same format with one more decimal place, for when the default is too coarse."""
    m = re.fullmatch(r"\.(\d+)(pct|f|e|g)(.*)", fmt or "")
    if m:
        return f".{int(m.group(1)) + 1}{m.group(2)}{m.group(3)}"
    x = raw.mean if isinstance(raw, Stat) else raw
    if isinstance(x, float):
        return ".4g"
    return None


def cite(ctx, key: str) -> dict:
    """Everything needed to cite ``key`` correctly; ``{"error": ...}`` if it isn't a key."""
    idx = ctx.idx
    opts = Options.from_config(ctx.cfg)
    e = idx.get(key)
    if e is None:
        p = idx.pending_for(key)
        if p is not None:
            return {"key": key, "kind": "pending", "snippets": [{"latex": "\\vouch{" + key + "}",
                    "renders": f"[pending: {key}]"}], "desc": p.get("desc") or "",
                    "producer": p.get("producer"), "note": "pending: the PDF shows a placeholder "
                    "until a run records it"}
        return {"key": key, "error": f"{key} is not a key",
                "suggestions": idx.suggest(key)}
    out: dict[str, Any] = {"key": key, "kind": e.kind, "desc": e.desc or "",
                           "better": e.better, "state": state_of(ctx, e), "origin": origin_of(e)}
    context = context_of(ctx, e)
    if context:
        out["context"] = context
    if e.kind == "claim":
        out["snippets"] = [{"latex": snippet(e), "renders": "the prose you write (its "
                            "provenance is the claim)"}]
        out["holds"] = bool(e.raw)
        if e.extra.get("explanation"):
            out["because"] = e.extra["explanation"]
        if isinstance(e.extra.get("margin"), (int, float)):
            out["margin"] = e.extra["margin"]
        return out
    if e.kind == "table":
        from .explore import tabular_for
        t = idx.tables[key]
        out["snippets"] = [{"latex": snippet(e), "renders": f"{len(t.rows)} body rows"},
                           {"latex": tabular_for(t), "renders": "a booktabs tabular around it"}]
        return out
    snippets = [{"latex": snippet(e), "renders": shown(e, opts),
                 "fmt": e.fmt or "(default)"}]
    finer = _finer(e.fmt, e.raw)
    if finer:
        snippets.append({"latex": snippet(e, finer), "renders": shown(e, opts, finer),
                         "fmt": finer})
    out["snippets"] = snippets
    subs = sorted((c for c in idx.entries.values()
                   if c.parent == key and c.kind in ("stat-field", "element")),
                  key=lambda c: (STAT_FIELDS.index(c.key.rsplit(".", 1)[1])
                                 if c.key.rsplit(".", 1)[1] in STAT_FIELDS else 99, c.key))
    if subs:
        out["subfields"] = [{"key": c.key, "renders": shown(c, opts)} for c in subs]
    if e.extra.get("alias_of"):
        out["alias_of"] = e.extra["alias_of"]
    return out


# ---------------------------------------------------------------------------
# compare: the arithmetic, and the code that makes it citable
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > tiny else tiny)
        c = 1 + aa / c
        c = c if abs(c) > tiny else tiny
        de = d * c
        h *= de
        if abs(de - 1) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                  + a * math.log(x) + b * math.log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def welch(a: Stat, b: Stat) -> tuple[float, float, float] | None:
    """(t, degrees of freedom, two-sided p) of Welch's t-test; None if it can't be computed."""
    if a.n < 2 or b.n < 2:
        return None
    va, vb = a.std ** 2 / a.n, b.std ** 2 / b.n
    se = math.sqrt(va + vb)
    if se == 0:
        return (math.inf if a.mean != b.mean else 0.0, float(a.n + b.n - 2),
                0.0 if a.mean != b.mean else 1.0)
    t = (a.mean - b.mean) / se
    dof = (va + vb) ** 2 / (va ** 2 / (a.n - 1) + vb ** 2 / (b.n - 1))
    p = betainc(dof / 2, 0.5, dof / (dof + t * t))
    return t, dof, p


def _segments(a: str, b: str) -> tuple[str, str, str]:
    """(common prefix, the part that differs in a, in b) of two keys."""
    sa, sb = a.split("."), b.split(".")
    i = 0
    while i < min(len(sa), len(sb)) and sa[i] == sb[i]:
        i += 1
    j = 0
    while j < min(len(sa), len(sb)) - i and sa[-1 - j] == sb[-1 - j]:
        j += 1
    da = "_".join(sa[i:len(sa) - j]) or sa[-1]
    db = "_".join(sb[i:len(sb) - j]) or sb[-1]
    return ".".join(sa[:i]), da, db


def compare(ctx, a: str, b: str) -> dict:
    idx = ctx.idx
    ea, eb = idx.get(a), idx.get(b)
    for k, e in ((a, ea), (b, eb)):
        if e is None:
            return {"error": f"{k} is not a key", "suggestions": idx.suggest(k)}
        if isinstance(e.raw, bool) or not isinstance(e.raw, (int, float, Stat)):
            return {"error": f"{k} is not a number ({type(e.raw).__name__})"}
    xa = ea.raw.mean if isinstance(ea.raw, Stat) else float(ea.raw)
    xb = eb.raw.mean if isinstance(eb.raw, Stat) else float(eb.raw)
    ka = a + ".mean" if isinstance(ea.raw, Stat) else a
    kb = b + ".mean" if isinstance(eb.raw, Stat) else b
    opts = Options.from_config(ctx.cfg)
    pct = "pct" in (ea.fmt or "") or "pct" in (eb.fmt or "")
    better = ea.better or eb.better
    out: dict[str, Any] = {"a": a, "b": b, "shown_a": shown(ea, opts), "shown_b": shown(eb, opts),
                           "difference": xa - xb, "ratio": xa / xb if xb else None,
                           "relative": (xa - xb) / abs(xb) if xb else None, "better": better}
    if better in ("higher", "lower") and xa != xb:
        out["winner"] = a if (xa > xb) == (better == "higher") else b
    if isinstance(ea.raw, Stat) and isinstance(eb.raw, Stat):
        sa, sb = ea.raw, eb.raw
        pooled = math.sqrt((sa.std ** 2 + sb.std ** 2) / 2)
        out["pooled_std_apart"] = abs(xa - xb) / pooled if pooled else math.inf
        w = welch(sa, sb)
        if w is not None:
            out["welch"] = {"t": w[0], "dof": w[1], "p": w[2], "n": [sa.n, sb.n]}
    prefix, da, db = _segments(a, b)
    base = f"{prefix}." if prefix else ""
    diff_key = f"{base}{da}_vs_{db}.{'pts' if pct else 'diff'}"
    scale = "100 * " if pct else ""
    unit = ', unit="points"' if pct else ""
    fmt = ".1f" if pct else (ea.fmt or ".3g")
    derive_code = (f'@vouch.derive("{diff_key}", fmt="{fmt}"{unit}'
                   + (f', better="{better}"' if better else "") + ",\n"
                   f'              desc="{a} minus {b}' + (', percentage points' if pct else "") + '")\n'
                   f'def _(v):\n    return {scale}(v["{ka}"] - v["{kb}"])\n')
    if "winner" in out:
        win, lose = (a, b) if out["winner"] == a else (b, a)
        kw, kl = (ka, kb) if win == a else (kb, ka)
        _, dw, dl = _segments(win, lose)
        claim_key = f"{base}{dw}_beats_{dl}"
        op = "gt" if better == "higher" else "lt"
        claim_desc = f"{win} {'>' if op == 'gt' else '<'} {lose} ({better} is better)"
    else:
        claim_key = f"{base}{da}_{'gt' if xa > xb else 'lt'}_{db}"
        op = "gt" if xa > xb else "lt"
        kw, kl = ka, kb
        claim_desc = f"{a} {'>' if op == 'gt' else '<'} {b}"
    claim_code = (f'@vouch.claim("{claim_key}", desc="{claim_desc}")\n'
                  f'def _(v):\n    return vouch.{op}(v["{kw}"], v["{kl}"])\n')
    out.update({"derive_key": diff_key, "claim_key": claim_key, "code": derive_code + "\n\n" +
                claim_code, "cite": f"\\vouchclaim{{{claim_key}}}{{...}} by "
                                    f"\\vouch{{{diff_key}}}" + (" points" if pct else "")})
    return out


def write_definitions(cfg, code: str, keys: list[str]) -> tuple[Path, list[str]]:
    """Append ``code`` to the first values module; returns (path, keys already defined)."""
    from .derived import static_keys
    mods = cfg.get("python", "values_modules", []) or ["vouch_values.py"]
    path = cfg.root / mods[0]
    existing = static_keys([path]) if path.is_file() else set()
    clash = [k for k in keys if k in existing]
    if clash:
        return path, clash
    text = path.read_text(encoding="utf-8") if path.is_file() else \
        '"""Values computed from recorded results; `vouch build` evaluates this file."""\n\nimport vouch\n'
    if "import vouch" not in text:
        text = "import vouch\n" + text
    path.write_text(text.rstrip("\n") + "\n\n\n" + code.rstrip("\n") + "\n", encoding="utf-8",
                    newline="\n")
    return path, []


# ---------------------------------------------------------------------------
# todo: the experiments the paper is still owed
# ---------------------------------------------------------------------------

def todo(ctx) -> list[dict]:
    idx = ctx.idx
    cites: dict[str, list[str]] = {}
    for pl in ctx.plans:
        for c in pl.doc.citations:
            p = idx.pending_for(c.key)
            if p is not None and idx.get(c.key) is None:
                root = next(k for k in idx.pending if c.key == k or c.key.startswith(k + "."))
                cites.setdefault(root, []).append(f"{c.file}:{c.line}")
    out = []
    for key, p in sorted(idx.pending.items()):
        if p.get("waits"):
            continue
        blocks = sorted(k for k, q in idx.pending.items() if key in (q.get("waits") or []))
        out.append({"key": key, "desc": p.get("desc") or "", "producer": p.get("producer"),
                    "declared_at": p.get("site"), "cited": cites.get(key, []),
                    "blocks": blocks, "blocked_cited": {k: cites.get(k, []) for k in blocks
                                                        if cites.get(k)}})
    return out


# ---------------------------------------------------------------------------
# describe / trace: everything known about a key, as data (MCP, trace --json)
# ---------------------------------------------------------------------------

def feeds(ctx, key: str) -> list[dict]:
    """Derived keys whose definitions read ``key`` (or something under it)."""
    idx = ctx.idx
    out = []
    for dk, d in sorted(((idx.derived_doc or {}).get("definitions") or {}).items()):
        for dep in d.get("deps") or {}:
            e = idx.get(dep)
            real = e.extra.get("alias_of", dep) if e is not None else dep
            if real == key or real.startswith(key + ".") or dep == key:
                out.append({"key": dk, "kind": d.get("kind", "value")})
                break
    return out


def cited_at(ctx, key: str) -> list[str]:
    e = ctx.idx.get(key)
    out = []
    for pl in ctx.plans:
        for c in pl.doc.citations:
            if c.key == key or (c.kind == "table" and e is not None and e.parent == c.key):
                where = f"{c.file}:{c.line}"
                if where not in out:
                    out.append(where)
    return out


def describe(ctx, key: str) -> dict:
    """Everything known about ``key``: value, provenance, freshness, citations."""
    from .tracked import call_text, timing_text
    from .values import encode
    idx = ctx.idx
    e = idx.get(key)
    if e is None:
        p = idx.pending_for(key)
        if p is not None:
            return {"key": key, "kind": "pending", "desc": p.get("desc") or "",
                    "producer": p.get("producer"), "declared_at": p.get("site"),
                    "waits_for": p.get("waits") or [], "cited_at": cited_at(ctx, key),
                    "cite": "\\vouch{" + key + "}"}
        return {"key": key, "error": f"{key} is not a key", "suggestions": idx.suggest(key)}
    opts = Options.from_config(ctx.cfg)
    out: dict[str, Any] = {"key": key, "kind": e.kind, "cite": snippet(e), "desc": e.desc or ""}
    if e.kind != "table":
        out["value"] = shown(e, opts)
        try:
            out["raw"] = dict(zip(("type", "value"), encode(e.raw)))
        except TypeError:
            out["raw"] = {"type": "repr", "value": repr(e.raw)}
    for f in ("fmt", "unit", "better", "site", "parent"):
        if getattr(e, f):
            out[f] = getattr(e, f)
    out["origin"] = origin_of(e)
    out["state"] = state_of(ctx, e)
    if e.kind == "claim":
        out["holds"] = bool(e.raw)
        for f in ("explanation", "margin"):
            if e.extra.get(f) is not None:
                out[f] = e.extra[f]
    if e.kind == "table" and key in idx.tables:
        t = idx.tables[key]
        out["table"] = {"columns": t.columns, "rows": len(t.rows),
                        "cells": f"{key}.<{t.row_key or 'row'}>.<column>"}
    call = e.extra.get("call")
    if call:
        out["call"] = {**call, "text": call_text(call)}
        if call.get("seconds"):
            out["call"]["timing"] = timing_text(call)
    context = context_of(ctx, e)
    if context:
        out["context"] = context
    if e.extra.get("derived"):
        out["derived"] = e.extra["derived"]
    for f in ("alias_of", "timing"):
        if e.extra.get(f):
            out[f] = e.extra[f]
    runs = source_runs(e)
    out["runs"] = []
    for r in runs:
        rec = idx.runs.get(r) or {}
        st = ctx.states.get(r)
        code = rec.get("code") or {}
        info = {"run": r, "state": st.state if st else "", "summary": st.summary() if st else "",
                "run_command": " ".join(rec.get("command") or []), "entry": rec.get("entry"),
                "started": rec.get("started"), "duration_s": rec.get("duration_s"),
                "git": rec.get("git"), "granularity": code.get("granularity"),
                "code_units": len(code.get("units") or {}),
                "inputs": sorted(rec.get("inputs") or {})}
        if rec.get("imported"):
            info["imported"] = rec["imported"]
        if st and st.reasons:
            info["reasons"] = [{"kind": x.kind, "subject": x.subject, "detail": x.detail}
                               for x in st.reasons]
        out["runs"].append(info)
    subs = [c for c in idx.entries.values() if c.parent == key and c.kind in ("stat-field", "element")]
    if subs:
        out["subfields"] = {c.key: shown(c, opts) for c in sorted(subs, key=lambda c: c.key)}
    out["feeds"] = feeds(ctx, e.extra.get("alias_of", key))
    out["cited_at"] = cited_at(ctx, key)
    change = ctx.pending.get(key)
    if change is not None:
        out["change"] = change.to_json()
    return out


def figure_info(ctx, rel: str) -> dict | None:
    """Where a figure came from: the run and savefig line, and whether the file on disk
    is still what that run saved. None if no run saved ``rel``."""
    fig = ctx.idx.figures.get(rel)
    if fig is None:
        return None
    rec = ctx.idx.runs.get(fig.run) or {}
    st = ctx.states.get(fig.run)
    kinds = {r.kind for r in (st.reasons if st else []) if r.subject == rel}
    return {"figure": rel, "run": fig.run, "state": st.state if st else "",
            "saved_at": fig.site, "hash": fig.hash,
            "file": "differs from what the run saved" if "tampered" in kinds else
                    "missing" if "absent" in kinds else "as the run saved it",
            "run_command": " ".join(rec.get("command") or []),
            "started": rec.get("started"), "git": (rec.get("git") or {}).get("commit"),
            "rerun": st.command if st and st.is_error else None,
            "cited_at": [f"{c.file}:{c.line}" for pl in ctx.plans for c in pl.doc.citations
                         if c.kind == "figure" and c.key == rel]}


def trace(ctx, target: str) -> dict:
    """A key; a tex ``file:line`` (what it cites); a figure (the run that saved it); or a
    script/file (the runs that used it)."""
    idx = ctx.idx
    if idx.get(target) is not None or idx.pending_for(target) is not None:
        return {"target": target, "type": "key", **describe(ctx, target)}
    if ":" in target and target.rsplit(":", 1)[1].isdigit() and target.split(":")[0].endswith(".tex"):
        f, line = target.rsplit(":", 1)
        hits = [c for pl in ctx.plans for c in pl.doc.citations
                if c.line == int(line) and (c.file == f or c.file.endswith("/" + f))]
        return {"target": target, "type": "line",
                "citations": [{"kind": c.kind, "key": c.key, "fmt": c.fmt, "via": c.via,
                               "file": c.file, "line": c.line,
                               "value": describe(ctx, c.key) if (idx.get(c.key) or
                                                                 idx.pending_for(c.key)) else None}
                              for c in hits]}
    cfg = ctx.cfg
    cands = {cfg.rel(target), Path(target).as_posix().removeprefix("./")}
    rel = next((c for c in cands if (cfg.root / c).exists()), cfg.rel(target))
    fig = figure_info(ctx, rel)
    if fig is not None:
        return {"target": target, "type": "figure", **fig}
    runs = [r for r, rec in sorted(idx.runs.items())
            if rec.get("entry") == rel or any(u.split("::")[0] == rel
                                              for u in (rec.get("code") or {}).get("units", {}))]
    if runs:
        return {"target": target, "type": "file", "file": rel, "runs": [
            {"run": r, "state": ctx.states[r].state if r in ctx.states else "",
             "values": [{"key": k, "cited_at": cited_at(ctx, k)} for k in
                        sorted(k for k, e in idx.entries.items() if e.run == r and e.kind == "value")]}
            for r in runs]}
    return {"target": target, "error": f"{target!r} is not a key, a tex file:line, a figure or a "
                                       f"file any run used", "suggestions": idx.suggest(target)}
