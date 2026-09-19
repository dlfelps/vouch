"""The bare-number lint: numbers typed into the paper instead of cited (SPEC §11.1).

Ported from asqc's ``check.lint`` and extended. Every number in the document body
that no ``\\vouch`` produces is a *literal*. Each literal is compared with every
recorded value, at the precision the literal itself claims (``93.2\\%`` claims one
decimal place), which splits them in two:

* ``bare-number`` -- it matches a recorded value: cite that key instead, so the
  number can't go stale (the fix names the exact ``\\vouch{...}`` to paste);
* ``no-source`` -- it matches nothing any run produced: a typo, a number from a
  run that no longer exists, or one that was never measured.

What is not a result is masked first: comments, vouch macro arguments, LaTeX
plumbing (``\\label``, ``\\ref``, ``\\cite``, lengths like ``0.5\\linewidth``,
tabular column specs, package options, ...). Integers are flagged only in text
mode, and only with two or more digits; years are skipped. ``[lint] allow`` rules
and a ``% vouch: ignore`` pragma exempt reviewed exceptions.
"""

from __future__ import annotations

import bisect
import dataclasses
import fnmatch
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from ..issues import Issue
from ..values import Stat
from .scan import Document, SHORT_ALIASES, VALUE_MACROS, balanced, line_of

# ---------------------------------------------------------------------------
# masking what is not a result
# ---------------------------------------------------------------------------

# command -> how many brace groups after it are plumbing (optional [..] groups too)
_PLUMBING_ARGS = {
    "label": 1, "ref": 1, "cref": 1, "Cref": 1, "autoref": 1, "eqref": 1, "pageref": 1,
    "nameref": 1, "vref": 1, "hyperref": 0, "nocite": 1, "bibliography": 1,
    "bibliographystyle": 1, "includegraphics": 1, "usepackage": 1, "RequirePackage": 1,
    "documentclass": 1, "input": 1, "include": 1, "subfile": 1, "import": 2,
    "subimport": 2, "setlength": 2, "addtolength": 2, "setcounter": 2, "addtocounter": 2,
    "stepcounter": 1, "hspace": 1, "vspace": 1, "url": 1, "href": 1, "definecolor": 3,
    "colorlet": 2, "color": 1, "textcolor": 1, "colorbox": 1, "date": 1,
    "graphicspath": 1, "multicolumn": 2, "multirow": 2, "cline": 1, "cmidrule": 1,
    "resizebox": 2, "scalebox": 1, "rule": 2, "raisebox": 1, "fontsize": 2,
    "hyphenation": 1, "hypersetup": 1, "geometry": 1, "captionsetup": 1, "numberwithin": 2,
    "newtheorem": 2, "pdfbookmark": 1, "setcitestyle": 1, "newcounter": 1,
    "linespread": 1, "vouchprovenance": 0,
}
_DEFINING = {"newcommand": 2, "renewcommand": 2, "providecommand": 2,
             "DeclareRobustCommand": 2, "NewDocumentCommand": 3, "RenewDocumentCommand": 3,
             "ProvideDocumentCommand": 3, "DeclareDocumentCommand": 3,
             "newenvironment": 3, "renewenvironment": 3, "DeclareMathOperator": 2}
_TABULAR_SPEC = {"tabular": 1, "array": 1, "longtable": 1, "tabulary": 2, "tabular*": 2,
                 "tabularx": 2, "wrapfigure": 2, "wraptable": 2, "minipage": 1,
                 "multicols": 1, "subfigure": 1, "subtable": 1}

_CMD = re.compile(r"\\([A-Za-z@]+\*?)")
_LENGTH = re.compile(
    r"-?\d*\.?\d+\s*(?:pt|em|ex|cm|mm|in|bp|sp|pc|dd|cc|mu|px)(?![A-Za-z])"
    r"|-?\d*\.?\d+\s*\\(?:text|line|column|paper)(?:width|height)|-?\d*\.?\d+\s*\\(?:baselineskip|hsize|vsize|parindent|parskip)")
_LINEBREAK_OPT = re.compile(r"\\\\\*?\s*\[[^\]]*\]")
_CITE = re.compile(r"cite[A-Za-z]*\*?")


def _blank(chars: list[str], start: int, end: int) -> None:
    for i in range(start, min(end, len(chars))):
        if chars[i] != "\n":
            chars[i] = " "


def _skip_groups(src: str, i: int, braces: int, parens: bool = False) -> int:
    """Index after the optional [..] (and (..)) groups and up to ``braces`` {..} groups."""
    n = 0
    while i < len(src):
        j = i
        while j < len(src) and src[j] in " \t\n":
            j += 1
        if j >= len(src):
            break
        if src[j] == "[":
            got = balanced(src, j, "[", "]")
        elif src[j] == "(" and parens:
            got = balanced(src, j, "(", ")")
        elif src[j] == "{" and n < braces:
            got = balanced(src, j)
            n += 1
        else:
            break
        if got is None:
            break
        i = got[1]
    return i


def mask(src: str, short: bool) -> str:
    """``src`` with everything that cannot be a typed-in result blanked (offsets kept)."""
    chars = list(src)
    macros = dict(VALUE_MACROS)
    if short:
        macros.update(SHORT_ALIASES)
    for m in _CMD.finditer(src):
        name = m.group(1)
        base = name.rstrip("*")
        start, i = m.start(), m.end()
        if name in macros or base in macros:
            # a claim's prose is prose: only its key is masked
            end = _skip_groups(src, i, 1)
            _blank(chars, start, end)
        elif base in _DEFINING or name == "def" or base in ("gdef", "edef", "xdef"):
            j = i
            while j < len(src) and src[j] in " \t":
                j += 1
            if j < len(src) and src[j] == "\\":                    # \newcommand\foo
                tok = _CMD.match(src, j)
                j = tok.end() if tok else j
                braces = max(_DEFINING.get(base, 2) - 1, 1)
            else:
                braces = _DEFINING.get(base, 2)
            if base in ("def", "gdef", "edef", "xdef"):
                k = src.find("{", j)
                got = balanced(src, k) if k >= 0 else None
                _blank(chars, start, got[1] if got else j)
            else:
                _blank(chars, start, _skip_groups(src, j, braces))
        elif base in _PLUMBING_ARGS or _CITE.fullmatch(name):
            n = _PLUMBING_ARGS.get(base, 1)
            _blank(chars, start, _skip_groups(src, i, n, parens=(base == "cmidrule")))
        elif base in ("begin", "end"):
            got = balanced(src, _skip_ws(src, i))
            if got is None:
                continue
            env = got[0].strip()
            end = got[1]
            if base == "begin":
                end = _skip_groups(src, end, _TABULAR_SPEC.get(env, 0))
            _blank(chars, start, end)
    masked = "".join(chars)
    for rx in (_LENGTH, _LINEBREAK_OPT):
        masked = rx.sub(lambda mm: re.sub(r"[^\n]", " ", mm.group(0)), masked)
    return masked


def _skip_ws(src: str, i: int) -> int:
    while i < len(src) and src[i] in " \t\n":
        i += 1
    return i


_MATH = re.compile(
    r"\$\$.*?\$\$|(?<!\\)\$.*?(?<!\\)\$|\\\(.*?\\\)|\\\[.*?\\\]"
    r"|\\begin\{(equation|align|alignat|gather|multline|flalign|eqnarray|math|displaymath)(\*?)\}"
    r".*?\\end\{\1\2\}", re.S)


def math_spans(src: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _MATH.finditer(src)]


# ---------------------------------------------------------------------------
# finding literals
# ---------------------------------------------------------------------------

_NUM = r"\d+(?:\.\d+)?"
_LITERAL = re.compile(
    rf"(?P<pm>(?<![\w.])-?{_NUM}\s*\\pm\s*{_NUM}(?:\s*\\%)?)"
    rf"|(?P<sci>(?<![\w.])-?{_NUM}\s*\\times\s*10\^\{{?-?\d+\}}?)"
    rf"|(?P<thousands>(?<![\w.])\d{{1,3}}(?:\{{,\}}\d{{3}})+(?:\.\d+)?(?:\s*\\%)?)"
    rf"|(?P<pct>(?<![\w.])-?{_NUM}\s*\\%)"
    rf"|(?P<mult>(?<![\w.]){_NUM}\s*\\times(?!\s*\d)(?![A-Za-z]))"
    rf"|(?P<dec>(?<![\w.])-?\d+\.\d+(?![\w.]))"
    rf"|(?P<int>(?<![\w.^_{{}}\-]){r'\d{2,}'}(?![\w.}}]))")
_YEAR = re.compile(r"(?:19|20)\d\d")


@dataclasses.dataclass
class Literal:
    file: str
    line: int
    offset: int
    text: str                          # as written, e.g. "93.2\\%"
    kind: str                          # pm sci thousands pct mult dec int
    numbers: list[tuple[float, int]]   # (value, implied decimal places)
    percent: bool
    math: bool
    context: str


def parse_numbers(text: str) -> list[tuple[float, int]]:
    """The numbers a literal shows, each with the decimal places it claims."""
    s = re.sub(r"\{,\}|\\%|\s|\$", "", text)
    s = s.replace("\\times", "x").replace("\\pm", ",")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)x10\^\{?(-?\d+)\}?", s)
    if m:
        exp = int(m.group(2))
        mant = m.group(1)
        return [(float(mant) * 10 ** exp, _places(mant) - exp)]
    out = []
    for part in s.rstrip("x").split(","):
        if part:
            out.append((float(part), _places(part)))
    return out


def _places(literal: str) -> int:
    return len(literal.split(".")[1]) if "." in literal else 0


def find_literals(doc: Document, *, allow_years: bool = True) -> list[Literal]:
    out: list[Literal] = []
    for path, masked in doc.masked.items():
        src = mask(masked, doc.short)
        start = doc.body_start.get(path, 0) if path == doc.main else 0
        maths = math_spans(src)
        starts = [a for a, _ in maths]
        rel = doc.rel(path)
        for m in _LITERAL.finditer(src, start):
            kind = m.lastgroup
            text = m.group(0)
            i = bisect.bisect_right(starts, m.start()) - 1
            in_math = i >= 0 and maths[i][0] <= m.start() < maths[i][1]
            if kind == "int" and (in_math or (allow_years and _YEAR.fullmatch(text))):
                continue
            lo, hi = max(0, m.start() - 60), min(len(src), m.end() + 60)
            out.append(Literal(rel, line_of(src, m.start()), m.start(), text.strip(), kind,
                               parse_numbers(text), "\\%" in text, in_math,
                               " ".join(src[lo:hi].split())))
    return out


# ---------------------------------------------------------------------------
# matching literals to recorded values
# ---------------------------------------------------------------------------

_KIND_RANK = {"value": 0, "param": 1, "stat-field": 2, "element": 3, "table-cell": 4}


@dataclasses.dataclass
class Match:
    key: str
    value: float
    scale: int          # 100 if the literal is the value in percent
    exact: bool


class Candidates:
    """Every recorded number, sorted, for fast "which value could this literal be"."""

    def __init__(self, idx):
        rows: list[tuple[float, int, str]] = []
        self.stats: list[tuple[str, Stat]] = []
        for key, e in idx.entries.items():
            if e.kind not in _KIND_RANK or e.extra.get("alias_of"):
                continue
            raw = e.raw
            if isinstance(raw, Stat):
                self.stats.append((key, raw))
                continue                       # its .mean/.std are entries of their own
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                continue
            rows.append((float(raw), _KIND_RANK[e.kind], key))
        rows.sort()
        self.values = [r[0] for r in rows]
        self.rows = rows

    def near(self, x: float, tol: float) -> list[tuple[float, int, str]]:
        lo = bisect.bisect_left(self.values, x - tol)
        hi = bisect.bisect_right(self.values, x + tol)
        return self.rows[lo:hi]


def rounds_to(candidate: float, shown: float, places: int) -> bool:
    """Does ``candidate``, printed at ``places`` decimals (half up), read as ``shown``?"""
    try:
        q = Decimal(1).scaleb(-places)
        return Decimal(repr(candidate)).quantize(q, rounding=ROUND_HALF_UP) == \
            Decimal(repr(shown)).quantize(q, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return False


def match(lit: Literal, cands: Candidates) -> tuple[list[Match], list[Match]]:
    """(exact matches, near misses) for a literal."""
    if lit.kind == "pm" and len(lit.numbers) == 2:
        (m, mp), (s, sp) = lit.numbers
        exact = []
        for key, st in cands.stats:
            for scale in ((100, 1) if lit.percent else (1, 100)):
                if rounds_to(st.mean * scale, m, mp) and rounds_to(st.std * scale, s, sp):
                    exact.append(Match(key, st.mean, scale, True))
        return exact, []
    exact: dict[str, Match] = {}
    near: dict[str, Match] = {}
    for value, places in lit.numbers[:1]:
        tol = 10.0 ** -places
        for scale in ((100, 1) if lit.percent else (1, 100)):
            for cand, _rank, key in cands.near(value / scale, tol / scale * 1.0001):
                if rounds_to(cand * scale, value, places):
                    exact.setdefault(key, Match(key, cand, scale, True))
                elif places >= 1 and key not in exact:
                    near.setdefault(key, Match(key, cand, scale, False))
            if exact and scale == (100 if lit.percent else 1):
                break                 # a match at the literal's own scale is enough
    rank = {k: r for _, r, k in cands.rows}

    def order(ms: dict[str, Match]) -> list[Match]:
        return sorted(ms.values(), key=lambda mm: (rank.get(mm.key, 9), mm.key))
    return order(exact), order(near)


def suggestion(lit: Literal, m: Match) -> str:
    """The ``\\vouch`` that prints what the literal shows."""
    places = lit.numbers[0][1] if lit.numbers else 0
    if lit.kind == "pm":
        return "\\vouch{" + m.key + "}"
    if lit.percent or m.scale == 100:
        return "\\vouch[." + str(max(places, 0)) + "pct]{" + m.key + "}"
    if lit.kind == "int" or places == 0:
        return "\\vouch[d]{" + m.key + "}" if float(m.value).is_integer() else \
            "\\vouch[.0f]{" + m.key + "}"
    return "\\vouch[." + str(places) + "f]{" + m.key + "}"


# ---------------------------------------------------------------------------
# the issues
# ---------------------------------------------------------------------------

def _allowed(lit: Literal, rules: list[dict], raw_line: str) -> bool:
    if re.search(r"%\s*vouch:\s*ignore\b", raw_line):
        return True
    for r in rules:
        files = r.get("files")
        if files and not any(fnmatch.fnmatch(lit.file, f) for f in ([files] if isinstance(files, str) else files)):
            continue
        try:
            if re.search(r.get("pattern", "(?!)"), lit.context):
                return True
        except re.error:
            continue
    return False


def lint(doc: Document, idx, cfg) -> list[Issue]:
    """``bare-number`` and ``no-source`` issues for one paper."""
    level = str(cfg.get("lint", "level", "warn") or "warn")
    if level == "off":
        return []
    severity = "error" if level == "error" else "warning"
    rules = [r for r in (cfg.get("lint", "allow", []) or []) if isinstance(r, dict)]
    cands = Candidates(idx)
    raw_lines: dict[str, list[str]] = {}
    for p, text in doc.raw.items():
        raw_lines[doc.rel(p)] = text.split("\n")
    out: list[Issue] = []
    seen: set[tuple[str, int, str]] = set()
    for lit in find_literals(doc, allow_years=bool(cfg.get("lint", "allow_years", True))):
        if (lit.file, lit.line, lit.text) in seen:
            continue
        seen.add((lit.file, lit.line, lit.text))
        lines = raw_lines.get(lit.file, [])
        raw_line = lines[lit.line - 1] if 0 < lit.line <= len(lines) else ""
        if _allowed(lit, rules, raw_line):
            continue
        exact, near = match(lit, cands)
        shown = lit.text.replace("\\%", "%").replace("{,}", ",")
        if exact:
            best = exact[0]
            others = ", ".join(m.key for m in exact[1:3])
            snippet = suggestion(lit, best)
            out.append(Issue("bare-number", severity,
                             f"{shown} is typed by hand; it is {best.key}"
                             + (f" (also {others})" if others else "") + f": cite it as {snippet}",
                             lit.file, lit.line, subject=lit.text, fix=snippet, fix_kind="edit",
                             detail={"literal": lit.text, "matches": [m.key for m in exact[:5]],
                                     "suggestion": snippet}))
        else:
            hint = ""
            if near:
                hint = (f"; the closest recorded value is {near[0].key} = "
                        f"{near[0].value * near[0].scale:.6g}, which doesn't round to it")
            out.append(Issue("no-source", severity,
                             f"{shown} matches no recorded value{hint}: record it where it is "
                             f"computed, or remove it", lit.file, lit.line, subject=lit.text,
                             fix="record the number in the run that computes it (or add a "
                                 "[lint] allow rule / % vouch: ignore if it is not a result)",
                             fix_kind="human",
                             detail={"literal": lit.text,
                                     "near": [m.key for m in near[:3]]}))
    return out
