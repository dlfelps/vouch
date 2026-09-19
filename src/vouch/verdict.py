"""Claim helpers: comparisons that explain themselves (SPEC §5.3).

A claim can return a plain ``bool``, but a ``Verdict`` says more::

    vouch.gt(v["cifar.resnet.acc"], v["cifar.vit.acc"])
    # Verdict(holds=True, explanation="0.932 > 0.912", margin=0.0219)

``margin`` is how far the claim is from flipping, relative to the boundary it is
measured against: ``gt(a, b)`` holds by ``(a - b) / |b|``. A claim that holds by
less than ``[changes] claim_margin`` (default 1%) is reported as *fragile*, so a
thin result is noticed before a re-run flips it. A failing verdict has a negative
margin: how far it is from holding.

A ``Stat`` is compared by its mean. Verdicts combine with ``all_of`` and
``any_of`` and behave as booleans, so ``if vouch.gt(a, b):`` works too.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

from .values import Stat


@dataclasses.dataclass(frozen=True)
class Verdict:
    holds: bool
    explanation: str
    margin: float | None = None

    def __bool__(self) -> bool:
        return self.holds


def _num(x: Any, what: str) -> float:
    if isinstance(x, Stat):
        return float(x.mean)
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        item = getattr(x, "item", None)          # numpy / torch scalars
        if callable(item):
            try:
                return _num(item(), what)
            except (TypeError, ValueError):
                pass
        raise TypeError(f"{what} compares numbers or Stats, not {type(x).__name__}")
    return float(x)


def _show(x: float) -> str:
    return f"{x:.4g}"


def _rel(diff: float, boundary: float, other: float) -> float:
    """``diff`` relative to the size of the boundary (or of the other side, if it is 0)."""
    scale = abs(boundary) or abs(other)
    if scale == 0:
        return math.copysign(math.inf, diff) if diff else 0.0
    return diff / scale


def _compare(a: Any, b: Any, op: str) -> Verdict:
    x, y = _num(a, op), _num(b, op)
    holds = {">": x > y, ">=": x >= y, "<": x < y, "<=": x <= y}[op]
    if math.isnan(x) or math.isnan(y):
        return Verdict(False, f"{_show(x)} {op} {_show(y)} (not a number)", None)
    diff = (x - y) if op in (">", ">=") else (y - x)
    return Verdict(holds, f"{_show(x)} {op} {_show(y)}", _rel(diff, y, x))


def gt(a: Any, b: Any) -> Verdict:
    """``a > b``."""
    return _compare(a, b, ">")


def ge(a: Any, b: Any) -> Verdict:
    """``a >= b``."""
    return _compare(a, b, ">=")


def lt(a: Any, b: Any) -> Verdict:
    """``a < b``."""
    return _compare(a, b, "<")


def le(a: Any, b: Any) -> Verdict:
    """``a <= b``."""
    return _compare(a, b, "<=")


def between(x: Any, lo: Any, hi: Any) -> Verdict:
    """``lo <= x <= hi``; the margin is the distance to the nearer bound."""
    low, high = ge(x, lo), le(x, hi)
    v = _num(x, "between")
    margins = [m for m in (low.margin, high.margin) if m is not None]
    return Verdict(low.holds and high.holds,
                   f"{_show(_num(lo, 'between'))} <= {_show(v)} <= {_show(_num(hi, 'between'))}",
                   min(margins) if margins else None)


def approx(x: Any, y: Any, rel: float = 0.05) -> Verdict:
    """``|x - y| <= rel * |y|``; the margin is how much of that tolerance is left."""
    a, b = _num(x, "approx"), _num(y, "approx")
    off = abs(a - b) / abs(b) if b else (0.0 if a == b else math.inf)
    return Verdict(off <= rel, f"{_show(a)} is within {off:.1%} of {_show(b)} (allowed {rel:.0%})",
                   rel - off)


def _parts(items: tuple) -> list[Verdict]:
    out = []
    for it in items:
        if isinstance(it, Verdict):
            out.append(it)
        else:
            b = bool(it)
            out.append(Verdict(b, "true" if b else "false", None))
    return out


def all_of(*items: Any) -> Verdict:
    """Holds when every part holds; as fragile as its most fragile part."""
    parts = _parts(items)
    margins = [p.margin for p in parts if p.margin is not None]
    return Verdict(all(p.holds for p in parts), " and ".join(p.explanation for p in parts),
                   min(margins) if margins else None)


def any_of(*items: Any) -> Verdict:
    """Holds when some part holds; its margin is the best holding part's."""
    parts = _parts(items)
    holding = [p.margin for p in parts if p.holds and p.margin is not None]
    failing = [p.margin for p in parts if not p.holds and p.margin is not None]
    margin = max(holding) if holding else (max(failing) if failing else None)
    return Verdict(any(p.holds for p in parts), " or ".join(p.explanation for p in parts), margin)


def as_verdict(result: Any) -> Verdict | None:
    """A claim function's result as a Verdict; None if it is neither a bool nor a Verdict."""
    if isinstance(result, Verdict):
        return result
    item = getattr(result, "item", None)
    if not isinstance(result, bool) and callable(item):
        try:
            result = item()
        except (TypeError, ValueError):
            return None
    if isinstance(result, bool):
        return Verdict(result, "true" if result else "false", None)
    return None
