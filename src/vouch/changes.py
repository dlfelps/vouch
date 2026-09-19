"""Change notification for cited values (SPEC §9).

A cited number that moves can make its sentence wrong ("the best of all models",
"roughly doubles"), or be the first visible sign of a bug. Every change to a cited
value is therefore *pending* until a person has re-read the sentences that cite it
and acknowledged it.

The baseline (``.vouch/acknowledged.json``, committed) holds the last acknowledged
state of every cited key. ``vouch build`` acknowledges automatically what cannot
have made prose wrong -- a key cited for the first time, a format change, a move
hidden by the printed rounding -- and logs it to ``.vouch/history.jsonl``.
"""

from __future__ import annotations

import dataclasses
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from .config import Config
from .freshness import now, who
from .index import Index, origin
from .store import atomic_write_text
from .values import Stat, encode

BASELINE = "acknowledged.json"
HISTORY = "history.jsonl"
NOTIFIED = "cache/notified.json"

PENDING = ("changed", "suspicious", "figure-changed")
AUTO = ("new", "hidden", "reformatted")


@dataclasses.dataclass
class Current:
    key: str
    kind: str                    # value | claim | figure
    type: str
    raw: Any                     # JSON-safe payload
    rendered: list[str]
    plain: list[str]
    source: str


@dataclasses.dataclass
class Citing:
    file: str
    line: int
    sentence: str
    table: str | None = None     # cited as a cell of this vouchtable


@dataclasses.dataclass
class Change:
    key: str
    cls: str                     # new hidden reformatted changed suspicious figure-changed
    reasons: list[str]
    old: dict | None
    new: Current
    citations: list[Citing]
    delta: float | None = None
    rel: float | None = None

    @property
    def pending(self) -> bool:
        return self.cls in PENDING

    def to_json(self) -> dict:
        return {"key": self.key, "class": self.cls, "reasons": self.reasons,
                "old": (self.old or {}).get("plain"), "new": self.new.plain,
                "old_raw": (self.old or {}).get("raw"), "new_raw": self.new.raw,
                "source": self.new.source, "delta": self.delta, "relative": self.rel,
                "citations": [{k: v for k, v in dataclasses.asdict(c).items() if v is not None}
                              for c in self.citations]}


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------

def load_baseline(cfg: Config) -> dict[str, dict]:
    path = cfg.store / BASELINE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("keys", {})
    except (OSError, ValueError):
        return {}


def save_baseline(cfg: Config, keys: dict[str, dict]) -> None:
    lines = ["{", '  "schema": "vouch/1",', '  "keys": {']
    items = sorted(keys.items())
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f"    {json.dumps(k)}: {json.dumps(v, sort_keys=True, ensure_ascii=False)}{comma}")
    lines += ["  }", "}", ""]
    atomic_write_text(cfg.store / BASELINE, "\n".join(lines))


def append_history(cfg: Config, events: list[dict]) -> None:
    if not events:
        return
    cfg.store.mkdir(parents=True, exist_ok=True)
    with open(cfg.store / HISTORY, "a", encoding="utf-8", newline="\n") as fh:
        for e in events:
            fh.write(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n")


def baseline_entry(cur: Current, *, by: str, why: str, when: str) -> dict:
    return {"kind": cur.kind, "type": cur.type, "raw": cur.raw, "rendered": cur.rendered,
            "plain": cur.plain, "source": cur.source, "acked": when, "by": by, "why": why}


# ---------------------------------------------------------------------------
# the current state of cited keys
# ---------------------------------------------------------------------------

def currents(idx: Index, plans) -> tuple[dict[str, Current], dict[str, list[Citing]]]:
    """The present state of every key the papers cite, and where each is cited."""
    cur: dict[str, Current] = {}
    cites: dict[str, list[Citing]] = {}
    for pl in plans:
        doc = pl.doc
        fmts: dict[str, list[str]] = {}
        for c in doc.citations:
            keys = [c.key]
            if c.kind == "table" and c.key in idx.tables:
                t = idx.tables[c.key]
                keys = [t.cell_key(i, col) for i in range(len(t.rows)) for col in t.columns]
            for k in keys:
                cites.setdefault(k, [])
                where = Citing(c.file, c.line, doc.sentence(c), c.key if k != c.key else None)
                if all((w.file, w.line) != (where.file, where.line) for w in cites[k]):
                    cites[k].append(where)
                if c.kind in ("value", "raw") or k != c.key:
                    fl = fmts.setdefault(k, [])
                    f = (c.fmt or "") if k == c.key and c.kind == "value" else ""
                    if f not in fl:
                        fl.append(f)
            if c.kind == "figure":
                fig = idx.figures.get(c.key)
                if fig is not None:
                    cur[c.key] = Current(c.key, "figure", "figure", fig.hash, [], [],
                                         f"run:{fig.run}")
        for k, fl in fmts.items():
            e = idx.get(k)
            if e is None or e.kind in ("claim", "table"):
                continue
            kind, payload = encode(e.raw)
            fl = sorted(fl)
            rendered = [pl.rendered[(k, f)].latex for f in fl if (k, f) in pl.rendered]
            plain = [pl.rendered[(k, f)].plain for f in fl if (k, f) in pl.rendered]
            if k in cur:          # cited by several papers: merge their formats
                rendered = sorted(set(cur[k].rendered) | set(rendered))
                plain = sorted(set(cur[k].plain) | set(plain))
            cur[k] = Current(k, "value", kind, payload, rendered, plain, origin(e))
        for c in doc.citations:
            if c.kind == "claim":
                e = idx.get(c.key)
                if e is not None and e.kind == "claim":
                    word = "HOLDS" if e.raw else "FALSE"
                    if e.extra.get("explanation"):
                        word += f" ({e.extra['explanation']})"
                    raw = {"holds": bool(e.raw), "values": e.extra.get("values") or {}}
                    if e.extra.get("margin") is not None:
                        raw["margin"] = e.extra["margin"]
                    cur[c.key] = Current(c.key, "claim", "claim", raw, [word], [word], origin(e))
    return cur, cites


# ---------------------------------------------------------------------------
# comparing
# ---------------------------------------------------------------------------

def _same(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b or math.isclose(a, b, rel_tol=1e-12, abs_tol=0.0)
    return a == b


def _primary(kind: str, raw: Any) -> list[float]:
    """The numbers a change is judged on: the value, a Stat's mean, a tuple's items."""
    def f(x):
        if isinstance(x, dict) and "$float" in x:
            return float(x["$float"])
        return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None
    if kind in ("int", "float"):
        v = f(raw)
        return [v] if v is not None else []
    if kind == "stat" and isinstance(raw, dict):
        v = f(raw.get("mean"))
        return [v] if v is not None else []
    if kind == "tuple" and isinstance(raw, list):
        return [v for v in (f(x) for x in raw) if v is not None]
    return []


def heuristics(old: dict, new: Current, rel_threshold: float) -> tuple[list[str], float | None, float | None]:
    """Why a change looks like a possible problem, plus its delta and relative size."""
    reasons: list[str] = []
    if old.get("source") != new.source:
        reasons.append(f"now produced by {new.source.split(':', 1)[1]} "
                       f"(was {str(old.get('source', '?')).split(':', 1)[-1]})")
    if old.get("type") != new.type:
        reasons.append(f"type changed ({old.get('type')} -> {new.type})")
        return reasons, None, None
    a, b = _primary(new.type, old.get("raw")), _primary(new.type, new.raw)
    delta = rel = None
    if a and b and len(a) == len(b):
        worst = 0.0
        for x, y in zip(a, b):
            if not math.isfinite(y):
                reasons.append("the new value is not finite")
                continue
            if not math.isfinite(x):
                continue
            if (x > 0 > y) or (x < 0 < y) or ((x == 0) != (y == 0)):
                reasons.append("sign flip" if x and y else "moved to or from zero")
            if x != 0:
                r = (y - x) / abs(x)
                worst = max(worst, abs(r))
                if rel is None or abs(r) > abs(rel):
                    rel, delta = r, y - x
                if x * y > 0 and abs(math.log10(y / x)) >= 1:
                    reasons.append("order-of-magnitude change")
        if worst > rel_threshold:
            reasons.append(f"large move ({worst:.0%})")
    if new.type == "stat" and isinstance(old.get("raw"), dict) and isinstance(new.raw, dict) \
            and old["raw"].get("n") != new.raw.get("n"):
        reasons.append(f"sample size changed (n={old['raw'].get('n')} -> {new.raw.get('n')})")
    return list(dict.fromkeys(reasons)), delta, rel


def compute(cfg: Config, baseline: dict[str, dict], current: dict[str, Current],
            cites: dict[str, list[Citing]]) -> list[Change]:
    threshold = float(cfg.get("changes", "rel_threshold", 0.10) or 0.10)
    out: list[Change] = []
    for key in sorted(current):
        cur = current[key]
        old = baseline.get(key)
        where = cites.get(key, [])
        if old is None:
            out.append(Change(key, "new", [], None, cur, where))
            continue
        if cur.kind == "figure":
            if old.get("raw") != cur.raw:
                out.append(Change(key, "figure-changed", ["the figure file changed"], old, cur, where))
            continue
        same_raw = _same(old.get("raw"), cur.raw) and old.get("type") == cur.type
        same_text = sorted(old.get("rendered", [])) == sorted(cur.rendered)
        moved_source = old.get("source") != cur.source
        if same_raw and not moved_source:
            if not same_text:
                out.append(Change(key, "reformatted", [], old, cur, where))
            continue
        reasons, delta, rel = heuristics(old, cur, threshold)
        if same_raw and moved_source:
            out.append(Change(key, "suspicious", reasons, old, cur, where))
        elif same_text and not reasons and cur.kind == "value":
            out.append(Change(key, "hidden", ["moved within the printed rounding"], old, cur,
                              where, delta, rel))
        else:
            if cur.kind == "claim":
                was, now = bool((old.get("raw") or {}).get("holds")), bool(cur.raw.get("holds"))
                reasons = reasons or [("the claim no longer holds" if was else "the claim holds now")
                                      if was != now else "the values behind the claim moved"]
            out.append(Change(key, "suspicious" if reasons and cur.kind == "value" else "changed",
                              reasons, old, cur, where, delta, rel))
    return out


# ---------------------------------------------------------------------------
# acknowledging
# ---------------------------------------------------------------------------

def auto_acknowledge(cfg: Config, baseline: dict[str, dict], changes: list[Change]) -> list[Change]:
    """Record what can't have made prose wrong; return what was acknowledged."""
    done, events, when = [], [], now()
    for ch in changes:
        if ch.cls not in AUTO:
            continue
        why = {"new": "first cited", "hidden": "moved within the printed rounding",
               "reformatted": "format changed"}[ch.cls]
        baseline[ch.key] = baseline_entry(ch.new, by="vouch build", why=why, when=when)
        if ch.cls != "new":
            events.append({"when": when, "event": ch.cls, "key": ch.key,
                           "old": (ch.old or {}).get("raw"), "new": ch.new.raw,
                           "by": "vouch build", "why": why})
        done.append(ch)
    if done:
        save_baseline(cfg, baseline)
        append_history(cfg, events)
    return done


def acknowledge(cfg: Config, baseline: dict[str, dict], changes: list[Change], why: str) -> None:
    by, when = who(), now()
    events = []
    for ch in changes:
        baseline[ch.key] = baseline_entry(ch.new, by=by, why=why, when=when)
        events.append({"when": when, "event": "ack", "class": ch.cls, "key": ch.key,
                       "old": (ch.old or {}).get("raw"), "new": ch.new.raw, "by": by, "why": why})
    save_baseline(cfg, baseline)
    append_history(cfg, events)


# ---------------------------------------------------------------------------
# notifying
# ---------------------------------------------------------------------------

def _fingerprint(ch: Change) -> str:
    return json.dumps([ch.key, ch.new.raw, ch.new.source], sort_keys=True)


def notify(cfg: Config, changes: list[Change]) -> tuple[list[Change], str | None]:
    """Log newly detected pending changes and run ``on_change`` for them, once each.

    Returns (the new ones, an error message if the hook failed).
    """
    pending = [c for c in changes if c.pending]
    path = cfg.store / NOTIFIED
    try:
        seen = set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    fresh = [c for c in pending if _fingerprint(c) not in seen]
    if not fresh:
        return [], None
    when = now()
    append_history(cfg, [{"when": when, "event": "detected", "class": c.cls, "key": c.key,
                          "old": (c.old or {}).get("raw"), "new": c.new.raw, "reasons": c.reasons}
                         for c in fresh])
    error = None
    cmd = cfg.get("changes", "on_change", []) or []
    if cmd:
        payload = json.dumps({"schema": "vouch/1", "command": "changes",
                              "changes": [c.to_json() for c in fresh]})
        try:
            r = subprocess.run(list(cmd), input=payload, text=True, capture_output=True,
                               timeout=60, cwd=cfg.root)
            if r.returncode != 0:
                error = f"on_change hook exited {r.returncode}: {r.stderr.strip()[:200]}"
        except (OSError, subprocess.SubprocessError) as exc:
            error = f"on_change hook failed: {exc}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(seen | {_fingerprint(c) for c in fresh})), encoding="utf-8")
    return fresh, error


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------

def readable(text: str) -> str:
    """A plain rendered form as a terminal shows it: 93.2\\% -> 93.2%."""
    return text.replace(r"\%", "%").replace(r"\_", "_").replace(r"\&", "&").replace(r"\#", "#")


def describe_delta(ch: Change) -> str:
    """Δ in the units the paper prints: percentage points for percentages."""
    if ch.delta is None or ch.rel is None:
        return ""
    if any(r"\%" in p for p in ch.new.plain):
        return f"Δ {100 * ch.delta:+.3g} pts, {ch.rel:+.1%} relative"
    return f"Δ {ch.delta:+.4g}, {ch.rel:+.1%}"


def was_text(ch: Change) -> str:
    """The tooltip line for a pending change."""
    old = ch.old or {}
    was = " | ".join(readable(p) for p in old.get("plain", [])) or "?"
    return f"CHANGED: was {was} (acked {str(old.get('acked', ''))[:10]})"
