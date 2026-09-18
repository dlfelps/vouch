"""Rendering a raw value with a format into LaTeX (SPEC §6).

Every rendering produces two strings:

* ``latex`` -- what the paper typesets, e.g. ``\\ensuremath{93.2 \\pm 0.4}\\%``;
* ``plain`` -- the same number as text safe for PDF strings (bookmarks) and TeX
  comments, e.g. ``93.2 +/- 0.4\\%``.

Rounding is half-up on the decimal repr of the float (``0.125 -> 0.13``), which is
what a reader expects, not the round-half-even of ``format()``.
"""

from __future__ import annotations

import dataclasses
import decimal
import math
from decimal import Decimal
from typing import Any, Mapping

from .fmt import Fmt, parse
from .values import Stat

_CTX = decimal.Context(prec=80)
_ROUNDING = {"half_up": decimal.ROUND_HALF_UP, "half_even": decimal.ROUND_HALF_EVEN}

_TEX_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


class RenderError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Rendered:
    latex: str
    plain: str


@dataclasses.dataclass(frozen=True)
class Options:
    rounding: str = "half_up"
    default_float: str = ".3g"
    siunitx: bool = False
    named: Mapping[str, str] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg) -> "Options":
        return cls(rounding=cfg.get("format", "rounding", "half_up"),
                   default_float=cfg.get("format", "default_float", ".3g"),
                   siunitx=bool(cfg.get("format", "siunitx", False)),
                   named=dict(cfg.get("format", "named", {}) or {}))


def tex_escape(text: str) -> str:
    return "".join(_TEX_ESCAPES.get(c, c) for c in str(text))


# ---------------------------------------------------------------------------
# numbers
# ---------------------------------------------------------------------------

def _dec(x: Any) -> Decimal:
    if isinstance(x, bool):
        return Decimal(int(x))
    if isinstance(x, int):
        return Decimal(x)
    return Decimal(repr(float(x)))


def _quantize(d: Decimal, places: int, rounding: str) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-places), rounding=_ROUNDING[rounding], context=_CTX)


def _group(int_part: str, sep: str) -> str:
    out = []
    for i, ch in enumerate(reversed(int_part)):
        if i and i % 3 == 0:
            out.append(sep)
        out.append(ch)
    return "".join(reversed(out))


def _round_sig(d: Decimal, sig: int, rounding: str) -> Decimal:
    if d == 0:
        return d
    exp = d.adjusted()
    return _quantize(d, sig - 1 - exp, rounding)


@dataclasses.dataclass
class _Num:
    negative: bool
    digits: str          # unsigned: "93.2", "18535"
    exponent: int | None = None   # scientific notation: digits x 10^exponent


def _format_number(x: Any, f: Fmt, rounding: str, *, scale: int = 1) -> _Num:
    """The digits of one number under ``f``; ``scale=100`` for percentages."""
    d = _dec(x) * scale
    t = f.type or "g"
    if t == "d":
        q = _quantize(d, 0, rounding)
        return _Num(q < 0, str(abs(q)))
    if t in ("f", "pct"):
        p = 6 if f.precision is None else f.precision
        q = _quantize(d, p, rounding)
        return _Num(q < 0, f"{abs(q):f}")
    if t == "e":
        p = 6 if f.precision is None else f.precision
        if d == 0:
            return _Num(False, _quantize(Decimal(0), p, rounding).__format__("f"), 0)
        q = _round_sig(d, p + 1, rounding)
        exp = q.adjusted()
        mant = _quantize(q.scaleb(-exp), p, rounding)
        return _Num(mant < 0, f"{abs(mant):f}", exp)
    if t == "g":
        p = 6 if f.precision is None else max(f.precision, 1)
        q = _round_sig(d, p, rounding)
        exp = q.adjusted() if q != 0 else 0
        if -4 <= exp < p:
            places = max(p - 1 - exp, 0)
            s = f"{abs(_quantize(q, places, rounding)):f}"
            if "." in s:
                s = s.rstrip("0").rstrip(".")
            return _Num(q < 0, s)
        mant = q.scaleb(-exp)
        s = f"{abs(_quantize(mant, p - 1, rounding)):f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return _Num(q < 0, s, exp)
    raise RenderError(f"format type {t!r} does not apply to numbers")


def _num_parts(n: _Num, f: Fmt) -> tuple[str, str, bool]:
    """(latex, plain, needs_math) for a formatted number, sign and grouping included."""
    digits = n.digits
    zero = all(c in "0." for c in digits)
    negative = n.negative and not zero            # never print -0.00
    int_part, dot, frac = digits.partition(".")
    if f.group:
        latex_digits = _group(int_part, "{,}") + dot + frac
        plain_digits = _group(int_part, ",") + dot + frac
    else:
        latex_digits = plain_digits = digits
    sign = "-" if negative else ("+" if f.sign else "")
    math_ = bool(sign)
    latex, plain = sign + latex_digits, sign + plain_digits
    if n.exponent is not None:
        latex += rf"\times10^{{{n.exponent}}}"
        plain += f"e{n.exponent}"
        math_ = True
    return latex, plain, math_


def _nonfinite(x: float) -> tuple[str, str, bool] | None:
    if math.isnan(x):
        return "NaN", "NaN", False
    if math.isinf(x):
        return (r"-\infty", "-inf", True) if x < 0 else (r"\infty", "inf", True)
    return None


def _scalar_core(x: Any, f: Fmt, opts: Options) -> tuple[str, str, bool]:
    if isinstance(x, float):
        nf = _nonfinite(x)
        if nf:
            return nf
    scale = 100 if f.type == "pct" else 1
    return _num_parts(_format_number(x, f, opts.rounding, scale=scale), f)


def _si_number(x: Any, f: Fmt, opts: Options) -> str:
    """A number as siunitx input: plain digits, e-notation, no grouping (siunitx groups)."""
    n = _format_number(x, f, opts.rounding, scale=100 if f.type == "pct" else 1)
    zero = all(c in "0." for c in n.digits)
    sign = "-" if n.negative and not zero else ("+" if f.sign else "")
    s = sign + n.digits
    if n.exponent is not None:
        s += f"e{n.exponent}"
    return s


# ---------------------------------------------------------------------------
# assembling values
# ---------------------------------------------------------------------------

def _math(latex: str, needs: bool) -> str:
    return rf"\ensuremath{{{latex}}}" if needs else latex


def _unit_tail(f: Fmt, unit: str | None) -> tuple[str, str]:
    if "u" in f.suffixes and unit:
        return rf"\,{tex_escape(unit)}", f" {tex_escape(unit)}"
    return "", ""


def resolve(spec: str | None, value: Any, opts: Options) -> Fmt:
    """The Fmt to use: explicit, named, or the default for the value's type."""
    if spec:
        if spec in opts.named:
            return parse(opts.named[spec])
        return parse(spec)
    if isinstance(value, bool) or isinstance(value, str):
        return Fmt(type="s")
    if isinstance(value, int):
        return Fmt(type="d")
    if isinstance(value, tuple) and value and all(isinstance(v, int) and not isinstance(v, bool)
                                                  for v in value):
        return Fmt(type="d")
    return parse(opts.default_float)


def render(value: Any, spec: str | None = None, *, unit: str | None = None,
           opts: Options | None = None) -> Rendered:
    """Render one raw value (decoded: int, float, bool, str, tuple, Stat)."""
    opts = opts or Options()
    f = resolve(spec, value, opts)

    if isinstance(value, (bool, str)) and f.type not in (None, "s"):
        raise RenderError(f"a {type(value).__name__} can't take a numeric format")
    if isinstance(value, bool):
        s = "true" if value else "false"
        return Rendered(s, s)
    if isinstance(value, str) or f.type == "s":
        s = tex_escape(str(value))
        return Rendered(s, s)
    if isinstance(value, Stat):
        return _render_stat(value, f, unit, opts)
    if isinstance(value, tuple):
        return _render_tuple(value, f, unit, opts)
    if isinstance(value, (int, float)):
        return _render_scalar(value, f, unit, opts)
    raise RenderError(f"cannot render a {type(value).__name__}")


def _render_scalar(x, f: Fmt, unit, opts: Options) -> Rendered:
    utail_l, utail_p = _unit_tail(f, unit)
    if opts.siunitx and not (isinstance(x, float) and not math.isfinite(x)):
        num = _si_number(x, f, opts)
        if f.type == "pct":
            latex = rf"\qty{{{num}}}{{\percent}}"
        elif "u" in f.suffixes and unit:
            latex = rf"\qty{{{num}}}{{{tex_escape(unit)}}}"
            utail_l = ""
        else:
            latex = rf"\num{{{num}}}"
        if "x" in f.suffixes:
            latex = rf"\ensuremath{{{latex}\times}}"
        _, plain, _ = _scalar_core(x, f, opts)
        pct = r"\%" if f.type == "pct" else ""
        return Rendered(latex + utail_l, plain + ("x" if "x" in f.suffixes else "") + pct + utail_p)
    core_l, core_p, needs = _scalar_core(x, f, opts)
    if "x" in f.suffixes:
        core_l += r"\times"
        core_p += "x"
        needs = True
    pct = r"\%" if f.type == "pct" else ""
    return Rendered(_math(core_l, needs) + pct + utail_l, core_p + pct + utail_p)


def _render_stat(s: Stat, f: Fmt, unit, opts: Options) -> Rendered:
    pct = r"\%" if f.type == "pct" else ""
    utail_l, utail_p = _unit_tail(f, unit)
    if "ci" in f.suffixes:
        ci = s.ci95
        m_l, m_p, m_math = _scalar_core(s.mean, f, opts)
        if ci is None:
            return Rendered(_math(m_l, m_math) + pct + utail_l, m_p + pct + utail_p)
        plain_f = dataclasses.replace(f, sign=False)
        lo_l, lo_p, lo_m = _scalar_core(ci[0], plain_f, opts)
        hi_l, hi_p, hi_m = _scalar_core(ci[1], plain_f, opts)
        latex = (_math(m_l, m_math) + pct + " [" + _math(lo_l, lo_m) + ", " + _math(hi_l, hi_m)
                 + "]" + utail_l)
        return Rendered(latex, f"{m_p}{pct} [{lo_p}, {hi_p}]{utail_p}")
    std_f = dataclasses.replace(f, sign=False)
    if opts.siunitx:
        num = f"{_si_number(s.mean, f, opts)} +- {_si_number(s.std, std_f, opts)}"
        if f.type == "pct":
            latex = rf"\qty{{{num}}}{{\percent}}"
        elif "u" in f.suffixes and unit:
            latex, utail_l = rf"\qty{{{num}}}{{{tex_escape(unit)}}}", ""
        else:
            latex = rf"\num{{{num}}}"
        m_l, m_p, _ = _scalar_core(s.mean, f, opts)
        sd_l, sd_p, _ = _scalar_core(s.std, std_f, opts)
        return Rendered(latex + utail_l, f"{m_p} +/- {sd_p}{pct}{utail_p}")
    m_l, m_p, _ = _scalar_core(s.mean, f, opts)
    sd_l, sd_p, _ = _scalar_core(s.std, std_f, opts)
    return Rendered(rf"\ensuremath{{{m_l} \pm {sd_l}}}" + pct + utail_l,
                    f"{m_p} +/- {sd_p}{pct}{utail_p}")


def _render_tuple(t: tuple, f: Fmt, unit, opts: Options) -> Rendered:
    pct = r"\%" if f.type == "pct" else ""
    utail_l, utail_p = _unit_tail(f, unit)
    parts = [_scalar_core(x, f, opts) for x in t]
    if "to" in f.suffixes and len(t) == 2:
        (a_l, a_p, _), (b_l, b_p, _) = parts
        return Rendered(rf"\ensuremath{{{a_l} \to {b_l}}}" + pct + utail_l,
                        f"{a_p} -> {b_p}{pct}{utail_p}")
    if len(t) == 2:
        if opts.siunitx:
            a, b = (_si_number(x, f, opts) for x in t)
            if f.type == "pct":
                latex = rf"\qtyrange{{{a}}}{{{b}}}{{\percent}}"
            else:
                latex = rf"\numrange{{{a}}}{{{b}}}"
            return Rendered(latex + utail_l, f"{parts[0][1]}-{parts[1][1]}{pct}{utail_p}")
        (a_l, a_p, a_m), (b_l, b_p, b_m) = parts
        return Rendered(_math(a_l, a_m) + "--" + _math(b_l, b_m) + pct + utail_l,
                        f"{a_p}-{b_p}{pct}{utail_p}")
    latex = ", ".join(_math(l, m) for l, _, m in parts) + pct + utail_l
    return Rendered(latex, ", ".join(p for _, p, _ in parts) + pct + utail_p)
