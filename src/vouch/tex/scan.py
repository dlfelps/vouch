"""Finding every citation in a paper, in reading order (SPEC §7.7).

The document is one logical source spread over files, so the scanner follows
``\\input``, ``\\include``, ``\\subfile`` and ``\\import`` from the main file and
reports citations in the order a reader meets them.

Two lessons from asqc's ``audit/numbers/check.py`` are built in:

* comments are blanked *with offsets preserved*, so line numbers stay true;
* a citation inside a macro definition counts only where the macro is used. A
  key hoisted into a ``\\newcommand`` that nothing uses reaches no reader.
"""

from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

VALUE_MACROS = {"vouch": "value", "vouchraw": "raw", "vouchclaim": "claim", "vouchtable": "table"}
SHORT_ALIASES = {"val": "value", "vclaim": "claim", "vtable": "table"}
GRAPHICS_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".pgf", ".svg")

_DEF_CMDS = ("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand",
             "NewDocumentCommand", "RenewDocumentCommand", "ProvideDocumentCommand",
             "DeclareDocumentCommand", "def", "gdef", "edef", "xdef")
_INPUT_CMDS = ("input", "include", "subfile", "import", "subimport")

_CMD_RE = re.compile(r"\\([A-Za-z@]+)\*?")


@dataclasses.dataclass
class Citation:
    kind: str              # value | raw | claim | table | figure
    key: str               # figures: the resolved project-relative path (or as written)
    fmt: str | None
    file: str              # project-relative
    line: int
    offset: int            # offset of the citing text in ``file``
    via: str | None = None # user macro the citation reached the text through
    written: str = ""      # the argument as written (figures: the raw path)
    resolved: bool = True  # figures: whether the file was found


@dataclasses.dataclass
class Document:
    main: Path
    root: Path
    files: list[Path]
    citations: list[Citation]
    short: bool
    body_start: dict[Path, int]
    missing_inputs: list[tuple[str, int, str]]
    raw: dict[Path, str]
    masked: dict[Path, str]

    def rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return p.resolve().as_posix()

    def sentence(self, c: Citation, limit: int = 240) -> str:
        path = self.root / c.file
        text = self.masked.get(path.resolve()) or self.masked.get(path)
        if text is None:
            return ""
        return sentence_at(text, c.offset, limit)


# ---------------------------------------------------------------------------
# masking
# ---------------------------------------------------------------------------

def _comment_start(line: str) -> int:
    """Index of the ``%`` that starts a comment in ``line``, or -1.

    A ``%`` is escaped by an odd number of backslashes: ``\\%`` is a percent sign,
    ``\\\\%`` is a line break followed by a comment.
    """
    i = line.find("%")
    while i != -1:
        n = 0
        j = i - 1
        while j >= 0 and line[j] == "\\":
            n += 1
            j -= 1
        if n % 2 == 0:
            return i
        i = line.find("%", i + 1)
    return -1


def mask_comments(src: str) -> str:
    out = []
    for line in src.split("\n"):
        i = _comment_start(line)
        out.append(line if i < 0 else line[:i] + " " * (len(line) - i))
    return "\n".join(out)


def _blank(src: str, start: int, end: int) -> str:
    seg = "".join("\n" if ch == "\n" else " " for ch in src[start:end])
    return src[:start] + seg + src[end:]


def mask_environments(src: str, envs) -> str:
    """Blank the contents of verbatim-like environments and ``\\verb`` spans."""
    for env in envs:
        pat = re.compile(r"\\begin\{" + re.escape(env) + r"\}(.*?)\\end\{" + re.escape(env) + r"\}",
                         re.S)
        for m in reversed(list(pat.finditer(src))):
            src = _blank(src, m.start(1), m.end(1))
    for m in reversed(list(re.finditer(r"\\verb\*?([^A-Za-z\s])(.*?)\1", src))):
        src = _blank(src, m.start(2), m.end(2))
    return src


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def _skip_ws(src: str, i: int) -> int:
    while i < len(src) and src[i] in " \t\r\n":
        i += 1
    return i


def balanced(src: str, i: int, open_: str = "{", close: str = "}") -> tuple[str, int] | None:
    """(contents, index after) of the group opening at ``src[i]``; None if absent/unbalanced."""
    if i >= len(src) or src[i] != open_:
        return None
    depth, start = 0, i
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{" and open_ != "{":
            sub = balanced(src, i)            # braces protect brackets: [a={]}]
            if sub is None:
                return None
            i = sub[1]
            continue
        if ch == open_:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return src[start + 1:i], i + 1
        i += 1
    return None


def _opt(src: str, i: int) -> tuple[str | None, int]:
    j = _skip_ws(src, i)
    got = balanced(src, j, "[", "]")
    return (got[0], got[1]) if got else (None, i)


def _arg(src: str, i: int) -> tuple[str | None, int]:
    j = _skip_ws(src, i)
    got = balanced(src, j)
    return (got[0], got[1]) if got else (None, i)


def line_of(src: str, offset: int) -> int:
    return src.count("\n", 0, offset) + 1


# ---------------------------------------------------------------------------
# sentences
# ---------------------------------------------------------------------------

_BOUNDARY_BACK = re.compile(r"(?:[.!?](?=\s)|\n[ \t]*\n)")
_BOUNDARY_FWD = re.compile(r"(?:[.!?](?=\s|$)|\n[ \t]*\n)")


def sentence_at(text: str, offset: int, limit: int = 240) -> str:
    """The sentence around ``offset``, whitespace collapsed, capped at ``limit`` chars."""
    start = 0
    for m in _BOUNDARY_BACK.finditer(text, 0, offset):
        start = m.end()
    m = _BOUNDARY_FWD.search(text, offset)
    end = m.end() if m and not m.group(0).startswith("\n") else (m.start() if m else len(text))
    s = " ".join(text[start:end].split())
    if len(s) > limit:
        rel = offset - start
        lo = max(0, min(rel - limit // 2, len(s) - limit))
        s = ("…" if lo else "") + s[lo:lo + limit].strip() + ("…" if lo + limit < len(s) else "")
    return s


# ---------------------------------------------------------------------------
# the scan
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _Def:
    name: str
    file: Path
    start: int     # start of the \newcommand statement
    body: tuple[int, int]


@dataclasses.dataclass
class _Event:
    offset: int
    kind: str      # cite | input | use
    data: object


class _Scanner:
    def __init__(self, main: Path, root: Path, skip_envs):
        self.main = main.resolve()
        self.root = root.resolve()
        self.main_dir = self.main.parent
        self.skip_envs = list(skip_envs)
        self.raw: dict[Path, str] = {}
        self.masked: dict[Path, str] = {}
        self.defs: dict[str, _Def] = {}
        self.missing: list[tuple[str, int, str]] = []
        self.graphicspath: list[str] = [""]
        self.short = False
        self.body_start: dict[Path, int] = {}

    def rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return p.resolve().as_posix()

    # -- files ------------------------------------------------------------------

    def load(self, path: Path) -> str | None:
        path = path.resolve()
        if path not in self.masked:
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            self.raw[path] = raw
            self.masked[path] = mask_environments(mask_comments(raw), self.skip_envs)
        return self.masked[path]

    def _resolve_input(self, cmd: str, args: list[str], current: Path) -> Path:
        if cmd in ("import", "subimport"):
            base = (current.parent if cmd == "subimport" else self.main_dir) / args[0]
            target = base / args[1]
        else:
            target = self.main_dir / args[0]
        if target.suffix != ".tex" and not target.exists():
            target = target.with_name(target.name + ".tex")
        elif cmd == "include" and target.suffix != ".tex":
            target = target.with_name(target.name + ".tex")
        return target

    # -- pass 1: definitions ---------------------------------------------------------

    def collect(self, path: Path, stack: tuple = ()) -> None:
        src = self.load(path)
        if src is None or path.resolve() in stack:
            return
        stack = stack + (path.resolve(),)
        m = re.search(r"\\begin\s*\{document\}", src)
        if m:
            self.body_start[path.resolve()] = m.end()
        opt = re.search(r"\\usepackage\s*\[([^\]]*)\]\s*\{vouch\}", src)
        if opt and "short" in {o.strip().split("=")[0] for o in opt.group(1).split(",")}:
            self.short = True
        for m in _CMD_RE.finditer(src):
            cmd = m.group(1)
            if cmd in _DEF_CMDS:
                d = self._parse_def(src, m, path)
                if d:
                    self.defs[d.name] = d
            elif cmd == "graphicspath":
                arg, _ = _arg(src, m.end())
                if arg is not None:
                    self.graphicspath = [""] + re.findall(r"\{([^{}]*)\}", arg)
            elif cmd in _INPUT_CMDS:
                target = self._input_target(src, m, path)
                if target is not None:
                    self.collect(target, stack)

    def _parse_def(self, src: str, m: re.Match, path: Path) -> _Def | None:
        cmd, i = m.group(1), m.end()
        i = _skip_ws(src, i)
        if cmd in ("def", "gdef", "edef", "xdef"):
            nm = re.match(r"\\([A-Za-z@]+)", src[i:])
            if not nm:
                return None
            name = nm.group(1)
            j = src.find("{", i + nm.end())
            if j < 0:
                return None
            got = balanced(src, j)
            if not got:
                return None
            return _Def(name, path.resolve(), m.start(), (j + 1, got[1] - 1))
        # \newcommand{\foo} or \newcommand\foo
        if i < len(src) and src[i] == "{":
            got = balanced(src, i)
            if not got:
                return None
            nm = re.match(r"\s*\\([A-Za-z@]+)\s*$", got[0])
            i = got[1]
        else:
            nm = re.match(r"\\([A-Za-z@]+)", src[i:])
            i = i + nm.end() if nm else i
        if not nm:
            return None
        if cmd.endswith("DocumentCommand"):
            _, i = _arg(src, i)                       # the argument spec
        else:
            for _ in range(2):                        # [n][default]
                opt, i = _opt(src, i)
                if opt is None:
                    break
        j = _skip_ws(src, i)
        got = balanced(src, j)
        if not got:
            return None
        return _Def(nm.group(1), path.resolve(), m.start(), (j + 1, got[1] - 1))

    def _input_target(self, src: str, m: re.Match, path: Path) -> Path | None:
        cmd = m.group(1)
        i = m.end()
        if cmd in ("import", "subimport"):
            a, i = _arg(src, i)
            b, i = _arg(src, i)
            if a is None or b is None:
                return None
            args = [a.strip(), b.strip()]
        else:
            a, _ = _arg(src, i)
            if a is None:
                if cmd != "input":
                    return None
                bare = re.match(r"\s+([^\s{}\\%]+)", src[i:])
                if not bare:
                    return None
                a = bare.group(1)
            args = [a.strip()]
        target = self._resolve_input(cmd, args, path)
        if not target.exists():
            self.missing.append((self.rel(path), line_of(src, m.start()), args[-1]))
            return None
        return target

    # -- pass 2: citations, in reading order -------------------------------------------

    def _in_def(self, path: Path, offset: int) -> _Def | None:
        for d in self.defs.values():
            if d.file == path.resolve() and d.start <= offset < d.body[1] + 1:
                return d
        return None

    def _cites_in(self, src: str, lo: int, hi: int, path: Path) -> list[tuple[int, Citation]]:
        """Citations made directly by text in src[lo:hi] (no macro indirection)."""
        found = []
        macros = dict(VALUE_MACROS)
        if self.short:
            macros.update(SHORT_ALIASES)
        for m in _CMD_RE.finditer(src, lo, hi):
            name = m.group(1)
            if name in macros:
                kind = macros[name]
                i = m.end()
                fmt = None
                if kind == "value":
                    fmt, i = _opt(src, i)
                key, _ = _arg(src, i)
                if key is None:
                    continue
                key = key.strip()
                found.append((m.start(), Citation(kind, key, (fmt or "").strip() or None,
                                                  self.rel(path), line_of(src, m.start()),
                                                  m.start(), written=key)))
            elif name == "includegraphics":
                _, i = _opt(src, m.end())
                arg, _ = _arg(src, i)
                if arg is None:
                    continue
                found.append((m.start(), Citation("figure", arg.strip(), None, self.rel(path),
                                                  line_of(src, m.start()), m.start(),
                                                  written=arg.strip())))
        return found

    def _macro_cites(self) -> dict[str, list[Citation]]:
        """For each user macro, the citations its expansion contains (transitively)."""
        direct: dict[str, list[Citation]] = {}
        uses: dict[str, list[str]] = {}
        names = set(self.defs)
        for name, d in self.defs.items():
            src = self.masked[d.file]
            lo, hi = d.body
            direct[name] = [c for _, c in self._cites_in(src, lo, hi, d.file)]
            uses[name] = [m.group(1) for m in _CMD_RE.finditer(src, lo, hi)
                          if m.group(1) in names and m.group(1) != name]
        out: dict[str, list[Citation]] = {}

        def expand(name: str, seen: frozenset) -> list[Citation]:
            if name in out:
                return out[name]
            got = list(direct.get(name, []))
            for other in uses.get(name, []):
                if other not in seen:
                    got += expand(other, seen | {other})
            out[name] = got
            return got

        for name in self.defs:
            expand(name, frozenset({name}))
        return {k: v for k, v in out.items() if v}

    def linearize(self, path: Path, macro_cites: dict, stack: tuple = ()) -> list[Citation]:
        src = self.masked.get(path.resolve())
        if src is None or path.resolve() in stack:
            return []
        stack = stack + (path.resolve(),)
        events: list[_Event] = []
        for off, c in self._cites_in(src, 0, len(src), path):
            if self._in_def(path, off) is None:
                events.append(_Event(off, "cite", c))
        for m in _CMD_RE.finditer(src):
            name = m.group(1)
            if name in macro_cites and self._in_def(path, m.start()) is None:
                events.append(_Event(m.start(), "use", name))
            elif name in _INPUT_CMDS and self._in_def(path, m.start()) is None:
                target = self._input_target(src, m, path)
                if target is not None:
                    events.append(_Event(m.start(), "input", target))
        events.sort(key=lambda e: e.offset)
        out: list[Citation] = []
        for e in events:
            if e.kind == "cite":
                out.append(e.data)
            elif e.kind == "use":
                for c in macro_cites[e.data]:
                    out.append(dataclasses.replace(c, file=self.rel(path),
                                                   line=line_of(src, e.offset),
                                                   offset=e.offset, via=e.data))
            else:
                out.extend(self.linearize(e.data, macro_cites, stack))
        return out

    def resolve_figure(self, c: Citation) -> Citation:
        written = c.written
        for prefix in self.graphicspath:
            base = self.main_dir / prefix / written
            cands = [base] if base.suffix.lower() in GRAPHICS_EXT else \
                [base.with_name(base.name + ext) for ext in GRAPHICS_EXT] + [base]
            for cand in cands:
                if cand.is_file():
                    return dataclasses.replace(c, key=self.rel(cand), resolved=True)
        return dataclasses.replace(c, key=written, resolved=False)


def scan(main: str | os.PathLike, root: str | os.PathLike,
         skip_envs=("verbatim", "lstlisting", "minted", "comment")) -> Document:
    """Scan a paper from its main file: citations in reading order, plus context."""
    s = _Scanner(Path(main), Path(root), skip_envs)
    if s.load(s.main) is None:
        raise FileNotFoundError(f"main file {main} not found")
    s.collect(s.main)
    s.missing.clear()                          # re-found (and deduplicated) during linearize
    cites = s.linearize(s.main, s._macro_cites())
    cites = [s.resolve_figure(c) if c.kind == "figure" else c for c in cites]
    files = list(s.masked)
    return Document(main=s.main, root=s.root, files=files, citations=cites, short=s.short,
                    body_start=s.body_start, missing_inputs=sorted(set(s.missing)),
                    raw=s.raw, masked=s.masked)
