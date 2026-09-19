"""Which functions did the run actually execute? (SPEC §8.2)

On ``import vouch`` a ``sys.monitoring`` (PEP 669) callback is registered for
``PY_START``. It returns ``DISABLE``, so it fires at most once per code object for
the whole process: the cost is one call per distinct function, whatever the run
does in its inner loops.

Two things are recorded:

* **executed units** -- ``file -> {qualname}``, with ``<locals>`` collapsed to the
  enclosing definition (``units.unit_of_qualname``);
* **source snapshots** -- each file's text *as it was when the run first loaded
  it*. A three-hour run whose model file is edited halfway through must record the
  code that ran, not the code on disk when it finished.

Precision is only claimed where it is safe; everything else is tracked whole:

* modules already imported when tracking started ran import-time code unobserved;
* the entry script, if anything before ``import vouch`` could have called
  first-party code (a call, decorator or base class naming the script's own
  definitions or a first-party import), or if vouch is imported inside a function;
* every file, if the run started child processes (they are not observed);
* Python < 3.12, no free monitoring tool id, or ``VOUCH_TRACE=0``: no tracking.
"""

from __future__ import annotations

import ast
import os
import site
import sys
import sysconfig
from time import perf_counter

from .units import read_source, unit_of_qualname
from .values import sanitize_key

_PKG_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
TOOL_IDS = (3, 4, 5, 2)        # leave 0 (debuggers) and 1 (coverage.py) alone


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _system_roots() -> tuple[str, ...]:
    roots = set()
    paths = sysconfig.get_paths()
    for k in ("stdlib", "platstdlib", "purelib", "platlib"):
        if paths.get(k):
            roots.add(_norm(paths[k]))
    for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix):
        if p:
            roots.add(_norm(p))
    try:
        for p in site.getsitepackages() + [site.getusersitepackages()]:
            roots.add(_norm(p))
    except Exception:  # pragma: no cover - site can be stripped down
        pass
    return tuple(r + os.sep for r in roots)


def _imports_vouch(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name == "vouch" or a.name.startswith("vouch.") for a in node.names)
    if isinstance(node, ast.ImportFrom):
        return bool(node.module) and (node.module == "vouch" or node.module.startswith("vouch."))
    return False


def _evaluated(node: ast.AST):
    """The nodes that are evaluated when ``node`` executes.

    A ``def`` evaluates its decorators and default values, not its body; a lambda
    evaluates nothing until called. A class body runs, so it is walked. Applying a
    decorator is a call, and so is subclassing (``__init_subclass__``, metaclasses):
    both are reported as calls.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        applied = list(node.decorator_list)
        if isinstance(node, ast.ClassDef):
            applied += node.bases
        for d in applied:
            yield ast.Call(func=d, args=[], keywords=[])
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for d in node.decorator_list + node.args.defaults + [k for k in node.args.kw_defaults if k]:
            yield from _evaluated(d)
        return
    if isinstance(node, ast.Lambda):
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _evaluated(child)


def _root_name(expr: ast.AST) -> str | None:
    while isinstance(expr, (ast.Attribute, ast.Call, ast.Subscript)):
        expr = expr.func if isinstance(expr, ast.Call) else expr.value
    return expr.id if isinstance(expr, ast.Name) else None


class Tracker:

    def __init__(self) -> None:
        self.active = False
        self.why_not: str | None = None
        self.tool: int | None = None
        self.executed: dict[str, set[str]] = {}
        self.snapshots: dict[str, str] = {}
        self.paths: dict[str, str] = {}          # normalized -> as Python saw it (keeps case)
        self.whole: dict[str, str] = {}          # file -> why it is tracked whole
        self.children = False
        self.internal = 0                        # >0 while vouch itself runs a subprocess (git)
        self._candidates: dict[str, bool] = {}
        self._roots: tuple[str, ...] = ()
        self.rules: list[dict] = []              # [[track]] in vouch.toml
        self.auto: dict = {}                     # code object -> Tracked
        self.pending: dict[int, tuple] = {}      # id(frame) -> (arguments, call site)
        self.problems: list[str] = []
        self._checked: set = set()
        self._cfg = None
        self._reported = False
        self._exit_hook = False

    # -- which files are worth watching ---------------------------------------------

    def _candidate(self, filename: str) -> bool:
        got = self._candidates.get(filename)
        if got is None:
            if not filename.endswith(".py") or filename.startswith("<"):
                got = False
            else:
                n = _norm(filename)
                got = not n.startswith(_PKG_DIR) and not n.startswith(self._roots)
            self._candidates[filename] = got
        return got

    def _snapshot(self, filename: str) -> None:
        n = _norm(filename)
        if n in self.snapshots:
            return
        self.paths.setdefault(n, os.path.abspath(filename))
        try:
            self.snapshots[n] = read_source(filename)
        except Exception:
            pass

    # -- the callback ----------------------------------------------------------------

    def _on_start(self, code, offset):
        try:
            fn = code.co_filename
            if self._candidate(fn):
                n = _norm(fn)
                self.executed.setdefault(n, set()).add(unit_of_qualname(code.co_qualname))
                if n not in self.snapshots:
                    self._snapshot(fn)
                if self.rules:
                    t = self.auto.get(code)
                    if t is None and code not in self._checked:
                        self._checked.add(code)
                        t = self._match(code)
                    if t is not None:
                        # a [[track]] function: keep firing, note this call's arguments
                        frame = sys._getframe(1)
                        from .tracked import arguments_from_frame
                        self.pending[id(frame)] = (arguments_from_frame(code, frame.f_locals),
                                                   self._site(frame.f_back), perf_counter())
                        return None
        except Exception:  # a tracking bug must never break the experiment
            pass
        return sys.monitoring.DISABLE

    def _on_return(self, code, offset, retval):
        t = self.auto.get(code)
        if t is not None:
            end = perf_counter()
            try:
                got = self.pending.pop(id(sys._getframe(1)), None)
                if got is not None:
                    t.record_safely(got[0], retval, got[1], end - got[2])
            except Exception:  # pragma: no cover
                pass

    # -- [[track]] in vouch.toml: results recorded without a decorator -----------------

    def configure(self, start_dir: str | None = None) -> None:
        """Load ``[[track]]`` rules from the project's vouch.toml."""
        from .config import Config, ConfigError, discover_root
        from pathlib import Path
        self.rules, self.problems = [], []
        if start_dir is None:                  # the entry script's project, else the cwd
            main_file = getattr(sys.modules.get("__main__"), "__file__", None)
            start_dir = os.path.dirname(os.path.abspath(main_file)) if main_file else None
        try:
            root, _ = discover_root(Path(start_dir) if start_dir else None)
            self._cfg = Config.load(root)
        except (ConfigError, OSError):
            return
        raw = self._cfg.data.get("track") or []
        if isinstance(raw, dict):
            raw = [raw]
        allowed = {"function", "over", "key", "returns", "time", "name", "fmt", "desc", "unit",
                   "better", "include", "exclude"}
        for entry in raw:
            if not isinstance(entry, dict) or "function" not in entry:
                self.problems.append("every [[track]] needs function = \"path.py::name\"")
                continue
            unknown = sorted(set(entry) - allowed)
            if unknown:
                self.problems.append(f"[[track]] {entry['function']!r}: unknown field(s) {unknown}")
            pats = entry["function"]
            pats = [pats] if isinstance(pats, str) else list(pats)
            over = entry.get("over", ())
            from .tracked import TrackError, check_returns, check_time
            try:
                returns = check_returns(entry.get("returns"), f"[[track]] {entry['function']!r} returns=")
                time_name, time_asked = check_time(entry.get("time"),
                                                   f"[[track]] {entry['function']!r} time=")
            except TrackError as exc:
                self.problems.append(str(exc))
                continue
            self.rules.append({"patterns": pats, "matched": 0, "returns": returns, "time": time_name,
                               "time_asked": time_asked,
                               "over": (over,) if isinstance(over, str) else tuple(over),
                               "key": entry.get("key"), "name": entry.get("name"),
                               "meta": {k: entry.get(k) for k in
                                        ("fmt", "desc", "unit", "better", "include", "exclude")}})
        if (self.rules or self.problems) and not self._exit_hook:
            import atexit
            atexit.register(self._report_at_exit)
            self._exit_hook = True

    def _site(self, frame) -> str:
        if frame is None or self._cfg is None:
            return "?"
        return f"{self._cfg.rel(frame.f_code.co_filename)}:{frame.f_lineno}"

    def _match(self, code):
        """The Tracked for a code object a [[track]] rule names, or None."""
        from fnmatch import fnmatchcase
        from .tracked import (DECORATED, Tracked, TrackError, check_names, function_name_of,
                            signature_order, template_fields)
        if code in DECORATED or self._cfg is None or code.co_name.startswith("<"):
            return None                                # modules, lambdas, comprehensions
        rel = self._cfg.rel(code.co_filename)
        qual = code.co_qualname
        for rule in self.rules:
            for pat in rule["patterns"]:
                fpat, sep, qpat = pat.partition("::")
                if not sep:
                    fpat, qpat = "*", fpat
                if not (fnmatchcase(rel, fpat) and fnmatchcase(qual, qpat)):
                    continue
                rule["matched"] += 1                       # named, even if it can't be tracked
                if code.co_flags & 0x220:                  # generator, async generator
                    self.problems.append(f"[[track]] {pat!r}: {qual} is a generator; decorate it "
                                         f"with @vouch.track or return a value instead")
                    return None
                names = signature_order(code)
                try:
                    check_names(set(rule["over"]), names, f"[[track]] {pat!r} over=", qual)
                    check_names(template_fields(rule["key"]), names, f"[[track]] {pat!r} key=", qual)
                except TrackError as exc:
                    self.problems.append(str(exc))
                    return None
                t = Tracked(name=sanitize_key(rule["name"]) if rule["name"] else function_name_of(qual),
                            qualname=qual, code=code, over=rule["over"], key=rule["key"],
                            returns=rule["returns"], time=rule["time"],
                            time_asked=rule["time_asked"], meta=rule["meta"], via="[[track]]")
                self.auto[code] = t
                sys.monitoring.set_local_events(self.tool, code, sys.monitoring.events.PY_RETURN)
                return t
        return None

    def unmatched(self) -> list[str]:
        """[[track]] patterns that named no function this process ran."""
        return [p for r in self.rules if not r["matched"] for p in r["patterns"]]

    def report(self, warn, final: bool = False) -> None:
        """Say what [[track]] couldn't do: problems as they are found (each run's end),
        and -- once the script is over (``final``) -- rules that never matched."""
        problems, self.problems = self.problems, []
        for msg in problems:
            warn(f"vouch.toml {msg}")
        if not final or self._reported or not self.rules:
            return
        self._reported = True
        if not self.active:
            warn(f"vouch.toml [[track]] needs function tracking ({self.why_not}); "
                 f"decorate the functions with @vouch.track instead")
            return
        for pat in self.unmatched():
            warn(f"vouch.toml [[track]] {pat!r} matched no function that ran")

    def _report_at_exit(self) -> None:
        from .api import _warn
        self.report(_warn, final=True)

    def _note_code(self, code) -> None:
        fn = code.co_filename
        if self._candidate(fn):
            self.executed.setdefault(_norm(fn), set()).add(
                unit_of_qualname(getattr(code, "co_qualname", code.co_name)))
            self._snapshot(fn)

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        if self.active:
            return
        self.configure()               # read even when tracking can't start: to say so
        if os.environ.get("VOUCH_TRACE") == "0":
            self.why_not = "tracking disabled (VOUCH_TRACE=0)"
            return
        mon = getattr(sys, "monitoring", None)
        if mon is None:
            self.why_not = f"Python {sys.version_info.major}.{sys.version_info.minor} has no sys.monitoring"
            return
        tool = next((t for t in TOOL_IDS if mon.get_tool(t) is None), None)
        if tool is None:
            self.why_not = "no free sys.monitoring tool id"
            return
        self._roots = _system_roots()
        try:
            mon.use_tool_id(tool, "vouch")
            mon.register_callback(tool, mon.events.PY_START, self._on_start)
            mon.register_callback(tool, mon.events.PY_RETURN, self._on_return)
            mon.set_events(tool, mon.events.PY_START)
        except Exception as exc:  # pragma: no cover
            self.why_not = f"sys.monitoring refused: {exc}"
            return
        self.tool, self.active = tool, True

        # what already ran: loaded modules (their import-time code) and the frames on
        # every thread's stack right now (a function that imports vouch is mid-call)
        for mod in list(sys.modules.values()):
            f = getattr(mod, "__file__", None)
            if f and self._candidate(f) and getattr(mod, "__name__", "") != "__main__":
                self.whole[_norm(f)] = "imported before vouch started tracking"
                self._snapshot(f)
        for frame in list(sys._current_frames().values()):
            while frame is not None:
                self._note_code(frame.f_code)
                frame = frame.f_back
        self._judge_main()
        self._watch_children()

    def _first_party_names(self, stmts: list[ast.stmt]) -> set[str]:
        """Names these statements bind to first-party code: the script's own functions
        and classes, and names imported from first-party modules."""
        names: set[str] = set()
        for s in stmts:
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(s.name)
            elif isinstance(s, ast.Import):
                for a in s.names:
                    mod = sys.modules.get(a.name)
                    if mod is not None and self._candidate(getattr(mod, "__file__", "") or ""):
                        names.add(a.asname or a.name.split(".")[0])
            elif isinstance(s, ast.ImportFrom) and s.module and not s.level:
                mod = sys.modules.get(s.module)
                if mod is not None and self._candidate(getattr(mod, "__file__", "") or ""):
                    names.update(a.asname or a.name for a in s.names)
        return names

    def _judge_main(self) -> None:
        """Track the entry script by function unless first-party code ran before
        ``import vouch`` (tracking can't have seen it)."""
        main = sys.modules.get("__main__")
        f = getattr(main, "__file__", None)
        if not f or not self._candidate(f):
            return
        n = _norm(f)
        self._snapshot(f)
        src = self.snapshots.get(n)
        frame = sys._getframe()
        while frame is not None and not (frame.f_globals is getattr(main, "__dict__", None)
                                         and frame.f_code.co_name == "<module>"):
            frame = frame.f_back
        try:
            tree = ast.parse(src) if src is not None else None
        except SyntaxError:
            tree = None
        if frame is None or tree is None:
            self.whole[n] = "could not tell what ran before `import vouch`"
            return
        line = frame.f_lineno
        current = next((s for s in tree.body
                        if s.lineno <= line <= (getattr(s, "end_lineno", None) or s.lineno)), None)
        if current is None or not any(_imports_vouch(x) for x in ast.walk(current)) \
                or isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.whole[n] = "vouch was imported from inside a function"
            return
        before = [s for s in tree.body if (getattr(s, "end_lineno", None) or s.lineno) < current.lineno]
        ours = self._first_party_names(before)
        for s in before:
            for node in _evaluated(s):
                if isinstance(node, ast.Call) and _root_name(node.func) in ours:
                    self.whole[n] = "code ran before `import vouch`"
                    return

    def _watch_children(self) -> None:
        """Notice child processes without importing anything.

        Importing ``multiprocessing`` here to patch it measurably slowed hot loops in
        the experiment (~8% on a tight pure-Python loop), so detection is lazy: audit
        hooks see process creation (fork, CreateProcess, a Python ``subprocess``), and
        ``children_started()`` reads multiprocessing's own counter at the end of the
        run if the experiment imported it.
        """
        tracker = self
        exe_name = os.path.basename(sys.executable).lower()

        def runs_python(*parts) -> bool:
            text = " ".join(str(p) for p in parts if p).lower()
            return "python" in text or exe_name in text

        def audit(event: str, args) -> None:
            if tracker.children or tracker.internal:
                return
            try:
                if event in ("os.fork", "os.forkpty"):
                    tracker.children = True                       # the child runs our code
                elif event == "_winapi.CreateProcess":            # (app name, command line, cwd)
                    tracker.children = runs_python(args[0], args[1])
                elif event == "os.posix_spawn":                   # (path, argv, env)
                    tracker.children = runs_python(args[0], *(args[1] or [])[:1])
                elif event == "os.spawn":                         # (mode, path, args, env)
                    tracker.children = runs_python(args[1])
                elif event == "subprocess.Popen":                 # (executable, args, cwd, env)
                    first = args[1][0] if isinstance(args[1], (list, tuple)) and args[1] else args[1]
                    tracker.children = runs_python(args[0], first)
            except Exception:
                pass
        try:
            sys.addaudithook(audit)
        except Exception:  # pragma: no cover
            pass

    def children_started(self) -> bool:
        if self.children:
            return True
        mpp = sys.modules.get("multiprocessing.process")
        counter = getattr(mpp, "_process_counter", None)
        if counter is not None:
            try:                                   # itertools.count(1) repr: "count(N)"
                return int(repr(counter)[6:-1]) > 1
            except ValueError:
                return False
        return False

    def stop(self) -> None:
        self.rules, self.problems = [], []       # nothing ran under us: nothing to report
        if not self.active:
            return
        mon = sys.monitoring
        try:
            mon.set_events(self.tool, 0)
            mon.register_callback(self.tool, mon.events.PY_START, None)
            mon.register_callback(self.tool, mon.events.PY_RETURN, None)
            mon.free_tool_id(self.tool)
        except Exception:  # pragma: no cover
            pass
        self.active = False
        self.why_not = "tracking stopped"

    # -- what the record needs ---------------------------------------------------------

    def granularity(self, wanted: str) -> tuple[str, str | None]:
        """("function" | "module", why not function)."""
        if wanted != "function":
            return "module", "configured"
        if not self.active and not self.executed:
            return "module", self.why_not or "tracking not started"
        if self.children_started():
            return "module", "the run started child processes, which are not tracked"
        return "function", None


tracker = Tracker()
