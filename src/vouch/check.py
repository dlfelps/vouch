"""``vouch check``: the gate (SPEC §11).

Read-only and fast: it never runs an experiment or user code. It asks, for the
paper as written:

* does every cited key exist, and do the generated files match what a build
  would write? (``unknown-key``, ``out-of-sync``)
* is every run the paper cites still the output of its code and data?
  (``stale``, ``upstream-stale``, ``tampered``)
* do the claims still hold, are the figures current, has any cited value moved
  since someone last read the prose around it? (``false-claim``, ``figure-*``,
  ``changed``/``suspicious``)

A stale run the paper cites nothing from is information, not a failure.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from . import changes as ch
from .build import BuildError, Context, comparable, plan
from .config import Config
from .index import source_runs
from .issues import Issue, apply_severity, ordered
from .values import is_finite_value, natural_key


@dataclasses.dataclass
class CheckReport:
    ctx: Context
    issues: list[Issue]
    summary: dict

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _cited(ctx: Context) -> dict[str, list[tuple[str, str, int]]]:
    """run id -> [(key, file, line)] for everything the papers cite from it."""
    out: dict[str, list[tuple[str, str, int]]] = {}
    for pl in ctx.plans:
        for c in pl.doc.citations:
            if c.kind == "figure":
                fig = ctx.idx.figures.get(c.key)
                runs = [fig.run] if fig else []
            else:
                e = ctx.idx.get(c.key)
                runs = source_runs(e) if e else []          # a derived value rests on its runs
                if e is not None and e.kind == "table" and c.key in ctx.idx.tables:
                    t = ctx.idx.tables[c.key]
                    runs = list(t.derived.get("runs") or []) if t.derived else runs
            for run in runs:
                out.setdefault(run, []).append((c.key, c.file, c.line))
    return out


def _where(items: list[tuple[str, str, int]], n: int = 12) -> list[tuple[str, int]]:
    seen, out = set(), []
    for _, f, ln in items:
        if (f, ln) not in seen:
            seen.add((f, ln))
            out.append((f, ln))
    return out[:n]


def _keys(items: list[tuple[str, str, int]], n: int = 4) -> str:
    ks = list(dict.fromkeys(k for k, _, _ in items))
    return ", ".join(ks[:n]) + (f" (+{len(ks) - n} more)" if len(ks) > n else "")


def collect(ctx: Context) -> list[Issue]:
    cfg, idx = ctx.cfg, ctx.idx
    issues: list[Issue] = list(ctx.project_issues)
    for pl in ctx.plans:
        issues += pl.issues

    # generated files vs what a build would write
    for pl in ctx.plans:
        for path, content in pl.files.items():
            rel = cfg.rel(path)
            try:
                disk = path.read_text(encoding="utf-8")
            except OSError:
                issues.append(Issue("out-of-sync", "error", f"{rel} has not been built",
                                    rel, fix="vouch build", fix_kind="build"))
                continue
            if comparable(path, disk) != comparable(path, content):
                issues.append(Issue("out-of-sync", "error",
                                    f"{rel} does not match the recorded values (run `vouch build`; "
                                    f"never edit generated files)", rel,
                                    fix="vouch build", fix_kind="build"))

    # runs
    cited = _cited(ctx)
    for run, st in sorted(ctx.states.items()):
        uses = cited.get(run, [])
        where = _where(uses)
        if st.is_error:
            if uses:
                fix = st.command or f"re-run {run}"
                if st.state == "stale":
                    fix += f"   (or, if the result cannot have changed: vouch accept {run} --why \"...\")"
                issues.append(Issue(st.state, "error", f"run {run}: {st.summary()}; the paper cites "
                                    f"{_keys(uses)}", subject=f"run:{run}", fix=fix,
                                    where_all=where,
                                    detail={"reasons": [dataclasses.asdict(r) for r in st.reasons]}))
            else:
                issues.append(Issue(st.state, "info", f"run {run}: {st.summary()} (nothing cited "
                                    f"from it)", subject=f"run:{run}"))
        elif st.state == "accepted" and uses:
            issues.append(Issue("accepted", "info", f"run {run}: {st.summary()}", subject=f"run:{run}"))
        elif st.state == "cosmetic" and uses:
            issues.append(Issue("cosmetic", "info", f"run {run}: code changed, but only outside "
                                "what it executed (comments, docstrings, formatting, or functions "
                                "it never called)", subject=f"run:{run}"))
        if not uses:
            continue
        drift = st.of("env-drift")
        if drift:
            issues.append(Issue("env-drift", "warning", f"run {run}: " + "; ".join(r.detail for r in drift),
                                subject=f"run:{run}", fix=st.command or None))
        for r in st.of("absent-input", "absent-artifact"):
            issues.append(Issue("absent", "warning", f"run {run}: {r.subject} is not on disk, so it "
                                "cannot be re-verified", subject=f"run:{run}"))
        imp = (st.recorded or {}).get("imported")
        if imp:
            producers = imp.get("producers") or []
            issues.append(Issue("imported", "info" if producers else "warning",
                                f"run {run} was imported from {imp.get('file')}, not recorded live"
                                + ("" if producers else "; no --producer was declared, so nothing "
                                   "ties its numbers to code"), subject=f"run:{run}",
                                fix=None if producers else
                                "re-record it by running the producer under vouch (or re-import "
                                "with --producer)", fix_kind="human"))
        git = (st.recorded or {}).get("git") or {}
        if git.get("dirty"):
            issues.append(Issue("dirty-tree-at-record", "info", f"run {run} was recorded with "
                                f"uncommitted changes; its code hashes still verify, but git "
                                f"{git.get('commit', '')[:7]} does not contain that code",
                                subject=f"run:{run}"))

    # claims, figures, values
    seen_keys: set[str] = set()
    margin_min = float(cfg.get("changes", "claim_margin", 0.01) or 0.0)
    for pl in ctx.plans:
        for c in pl.doc.citations:
            if c.kind == "claim":
                e = idx.get(c.key)
                if e is None or e.kind != "claim":
                    continue
                vals = e.extra.get("values") or {}
                expl = e.extra.get("explanation")
                about = expl or ", ".join(f"{k}={v}" for k, v in vals.items())
                if not e.raw:
                    issues.append(Issue("false-claim", "error",
                                        f"claim {c.key} no longer holds ({e.desc or 'no description'})"
                                        + (f": {about}" if about else ""),
                                        c.file, c.line, subject=c.key, fix_kind="human",
                                        fix="re-examine the result and rewrite the claim"))
                    continue
                margin = e.extra.get("margin")
                if isinstance(margin, (int, float)) and margin < margin_min and c.key not in seen_keys:
                    seen_keys.add(c.key)
                    issues.append(Issue("fragile", "warning",
                                        f"claim {c.key} holds by only {margin:.2%} ({about}); a "
                                        f"small change would flip it", c.file, c.line, subject=c.key,
                                        fix_kind="human",
                                        fix="soften the claim, or check it is not noise "
                                            "(more seeds, a significance test)"))
            elif c.kind == "figure" and c.resolved:
                fig = idx.figures.get(c.key)
                if fig is None:
                    issues.append(Issue("figure-untracked", "warning",
                                        f"{c.key} is not produced by any run (save it inside a run, "
                                        f"or declare it with run.artifact)", c.file, c.line,
                                        subject=c.key))
                    continue
                st = ctx.states.get(fig.run)
                if st and any(r.kind == "tampered" and r.subject == c.key for r in st.reasons):
                    issues.append(Issue("figure-tampered", "error",
                                        f"{c.key} differs from what run {fig.run} saved",
                                        c.file, c.line, subject=c.key, fix=st.command or None))
                elif st and st.is_error:
                    issues.append(Issue("figure-stale", "error",
                                        f"{c.key} comes from run {fig.run}, which is {st.state}",
                                        c.file, c.line, subject=c.key, fix=st.command or None))
            elif c.kind in ("value", "raw") and c.key not in seen_keys:
                seen_keys.add(c.key)
                e = idx.get(c.key)
                if e is None:
                    continue
                if not is_finite_value(e.raw):
                    issues.append(Issue("non-finite", "warning", f"{c.key} is {e.raw!r}",
                                        c.file, c.line, subject=c.key))
                if not e.desc:
                    issues.append(Issue("no-description", "warning",
                                        f"{c.key} has no description (add desc= or a [metrics] "
                                        f"pattern)", c.file, c.line, subject=c.key))

    # changes to cited values; cells cited only through a table are one issue per table
    by_table: dict[str, list] = {}
    for change in ctx.changes:
        via = {w.table for w in change.citations}
        if change.pending and len(via) == 1 and None not in via:
            by_table.setdefault(via.pop(), []).append(change)
    for table, cells in sorted(by_table.items()):
        flagged = [c for c in cells if c.cls == "suspicious"]
        shown = []
        for c in sorted(cells, key=lambda c: (c.cls != "suspicious", natural_key(c.key)))[:4]:
            old = " | ".join(ch.readable(p) for p in (c.old or {}).get("plain", [])) or "?"
            new = " | ".join(ch.readable(p) for p in c.new.plain) or "?"
            shown.append(f"{c.key.removeprefix(table + '.')} {old} -> {new}")
        more = f", ... (+{len(cells) - 4} more)" if len(cells) > 4 else ""
        where = sorted({(w.file, w.line) for c in cells for w in c.citations})
        issues.append(Issue("suspicious" if flagged else "changed", "warning",
                            f"table {table}: {len(cells)} cell(s) changed"
                            + (f", {len(flagged)} POSSIBLE PROBLEM(s)" if flagged else "")
                            + ": " + ", ".join(shown) + more,
                            subject=table, where_all=where, fix_kind="human",
                            fix=f"re-read the table and what the text says about it, then: "
                                f"vouch ack {table}",
                            detail={"cells": [c.to_json() for c in cells]}))
    grouped = {c.key for cells in by_table.values() for c in cells}
    for change in ctx.changes:
        if change.key in grouped:
            continue
        if change.new.kind == "claim" and not (change.new.raw or {}).get("holds", True):
            continue                  # false now: the false-claim error says it, and louder
        where = [(w.file, w.line) for w in change.citations]
        if change.pending and change.cls == "figure-changed":
            issues.append(Issue(change.cls, "warning", f"{change.key}: the figure file changed",
                                subject=change.key, where_all=where, fix_kind="human",
                                fix=f"look at the figure and re-read its caption and the text "
                                    f"about it, then: vouch ack {change.key}",
                                detail=change.to_json()))
        elif change.pending:
            old = " | ".join(ch.readable(p) for p in (change.old or {}).get("plain", [])) or "?"
            new = " | ".join(ch.readable(p) for p in change.new.plain) or "?"
            msg = f"{change.key}: {old} -> {new}"
            if change.reasons:
                msg += f" (POSSIBLE PROBLEM: {'; '.join(change.reasons)})" if change.cls == "suspicious" \
                    else f" ({'; '.join(change.reasons)})"
            issues.append(Issue(change.cls, "warning", msg, subject=change.key, where_all=where,
                                fix=f"re-read the sentences that cite it, then: vouch ack {change.key}",
                                fix_kind="human", detail=change.to_json()))
        else:
            issues.append(Issue(change.cls, "info",
                                f"{change.key}: {change.cls} (acknowledged at the next build)",
                                subject=change.key))

    # uncited values, as one line (a value a derivation reads is used)
    cited_keys = {c.key for pl in ctx.plans for c in pl.doc.citations}
    read = {k for e in idx.entries.values() for k in (e.extra.get("derived") or {}).get("deps", [])}
    read |= {k.rsplit(".", 1)[0] for k in read}
    uncited = [k for k, e in idx.entries.items()
               if e.kind == "value" and k not in cited_keys and k not in read
               and "alias_of" not in e.extra and not e.extra.get("timing")]
    if uncited:
        issues.append(Issue("unused-value", "info", f"{len(uncited)} recorded value(s) are not "
                            f"cited: " + ", ".join(sorted(uncited)[:5])
                            + (" ..." if len(uncited) > 5 else "")))
    return issues


def run_check(cfg: Config, *, strict: bool = False, check_env: bool = True) -> CheckReport:
    ctx = plan(cfg, check_env=check_env)
    issues = apply_severity(collect(ctx), cfg.get("check", "severity", {}), strict)
    cited = _cited(ctx)
    summary = {
        "papers": [cfg.rel(p.main) for p in ctx.plans],
        "citations": sum(len(p.doc.citations) for p in ctx.plans),
        "runs": len(ctx.states),
        "runs_cited": len(cited),
        "runs_fresh": sum(1 for r in cited if ctx.states.get(r) and not ctx.states[r].is_error),
        "claims_cited": sum(1 for p in ctx.plans for c in p.doc.citations if c.kind == "claim"),
        "pending_changes": sum(1 for c in ctx.changes if c.pending),
    }
    return CheckReport(ctx, ordered(issues), summary)
