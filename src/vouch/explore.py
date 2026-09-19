"""``vouch explore``: browse every recorded value and copy the LaTeX that cites it.

A read-only local web page, grouped the way the code is::

    experiment script  ->  function  ->  keys

A value recorded by ``@vouch.track`` (or ``[[track]]``) sits under its function;
one recorded with ``vouch.record``/``record_all`` under the function the call is
in, or the script's top level; derived values under ``vouch_values.py`` and their
definitions. Every row copies ``\\vouch{key}`` -- or ``\\vouchclaim``,
``\\vouchtable``, ``\\includegraphics`` -- with one click, and opens to show where
the value came from.

The server binds to 127.0.0.1 only, serves nothing but the page and its JSON, and
never runs project code (derived values come from ``derived.json``, as in
``vouch check``). It reads the store afresh when anything changes, and the page
polls, so re-running an experiment updates it in place. ``--html FILE`` writes the
same page as one self-contained file instead.
"""

from __future__ import annotations

import ast
import datetime as _dt
import hashlib
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import changes as ch
from .config import Config
from .index import Entry, Index, source_runs
from .render import Options, RenderError, render, tex_escape
from .values import STAT_FIELDS

PAGE = Path(__file__).parent / "data" / "explore.html"
DATA_SLOT = "/*__VOUCH_DATA__*/null"
_WORST = ("tampered", "incomplete", "stale", "upstream-stale", "accepted", "cosmetic", "fresh")


# ---------------------------------------------------------------------------
# where a line of code is: the function around it
# ---------------------------------------------------------------------------

class _Functions:
    """``file:line`` -> (qualname of the enclosing def, its line); ``<module>`` at top level."""

    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, list[tuple[int, int, str]] | None] = {}

    def _defs(self, rel: str) -> list[tuple[int, int, str]] | None:
        if rel not in self.cache:
            try:
                tree = ast.parse((self.root / rel).read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
                self.cache[rel] = None
                return None
            out: list[tuple[int, int, str]] = []

            def visit(node: ast.AST, prefix: str) -> None:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        qual = prefix + child.name
                        start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                        out.append((start, child.end_lineno or child.lineno, qual))
                        visit(child, qual + ".")
                    else:
                        visit(child, prefix)
            visit(tree, "")
            self.cache[rel] = out
        return self.cache[rel]

    def at(self, site: str | None) -> tuple[str, str, int]:
        """(file, qualname, line of the def) for a ``file:line`` site."""
        if not site or ":" not in site:
            return "", "<module>", 0
        rel, _, line = site.rpartition(":")
        if not line.isdigit():
            return rel, "<module>", 0
        n = int(line)
        best = None
        for start, end, qual in self._defs(rel) or []:
            if start <= n <= end and (best is None or start >= best[0]):
                best = (start, qual)
        return (rel, best[1], best[0]) if best else (rel, "<module>", 0)


# ---------------------------------------------------------------------------
# the registry, as the page shows it
# ---------------------------------------------------------------------------

def _citations(cfg: Config) -> tuple[dict[str, list[str]], list[Path], Path | None]:
    """key -> ["paper/main.tex:13", ...], the tex files read, and the first paper's directory."""
    from .build import BuildError, papers, paper_paths
    from .tex.scan import scan
    cites: dict[str, list[str]] = {}
    files: list[Path] = []
    first_dir = None
    try:
        chosen = papers(cfg)
    except BuildError:
        return cites, files, None
    for p in chosen:
        main = paper_paths(cfg, p)[0]
        if first_dir is None:
            first_dir = main.parent
        try:
            doc = scan(main, cfg.root, cfg.get("lint", "skip_envs", ()))
        except (OSError, FileNotFoundError):
            continue
        files += doc.files
        for c in doc.citations:
            where = f"{c.file}:{c.line}"
            got = cites.setdefault(c.key, [])
            if where not in got:
                got.append(where)
    return cites, files, first_dir


def _worst(states: dict, runs: list[str]) -> str:
    found = [states[r].state for r in runs if r in states]
    if not found:
        return ""
    return min(found, key=lambda s: _WORST.index(s) if s in _WORST else len(_WORST))


class _Builder:
    def __init__(self, cfg: Config):
        from .derive import prepare
        from .freshness import assess
        self.cfg = cfg
        self.idx = Index.load(cfg)
        self.issues = prepare(cfg, self.idx, build=False).issues
        self.states = assess(cfg, self.idx.runs, check_env=False)
        self.opts = Options.from_config(cfg)
        self.cites, self.tex_files, self.paper_dir = _citations(cfg)
        self.functions = _Functions(cfg.root)
        self.children: dict[str, list[Entry]] = {}
        for e in self.idx.entries.values():
            if e.kind in ("stat-field", "element") and e.parent:
                self.children.setdefault(e.parent, []).append(e)

    # -- one value ------------------------------------------------------------------

    def shown(self, e: Entry) -> str:
        if e.kind == "claim":
            return "holds" if e.raw else "FALSE"
        if e.kind == "table":
            return ""
        try:
            r = render(e.raw, e.fmt, unit=e.unit, opts=self.opts)
        except (RenderError, ValueError, TypeError):
            try:
                r = render(e.raw, None, unit=e.unit, opts=self.opts)
            except (RenderError, ValueError, TypeError):
                return str(e.raw)
        return ch.readable(r.plain).replace("+/-", "±")

    def item(self, e: Entry) -> dict[str, Any]:
        from .track import call_text, per_call_text
        it: dict[str, Any] = {"key": e.key, "kind": e.kind, "value": self.shown(e),
                              "desc": e.desc or "", "state": _worst(self.states, source_runs(e)),
                              "cited": self.cites.get(e.key, []), "run": e.run or ""}
        for f in ("unit", "fmt", "better", "site"):
            if getattr(e, f):
                it[f] = getattr(e, f)
        if e.kind == "claim":
            it["snippet"] = "\\vouchclaim{" + e.key + "}{" + tex_escape(e.desc or "") + "}"
            for f in ("explanation", "margin"):
                if e.extra.get(f) is not None:
                    it[f] = e.extra[f]
        elif e.kind == "table":
            it.update(self.table(e.key))
        else:
            it["snippet"] = "\\vouch{" + e.key + "}"
        subs = sorted(self.children.get(e.key, []), key=lambda c: _field_order(c.key[len(e.key) + 1:]))
        if subs:
            it["parts"] = [{"key": c.key, "name": c.key[len(e.key) + 1:], "value": self.shown(c),
                            "cited": self.cites.get(c.key, [])} for c in subs]
        call = e.extra.get("call")
        if call:
            it["call"] = call_text(call)
            each = per_call_text(call, limit=len(call.get("results") or []))
            if each:
                it["each"] = each
            it["sites"] = list(call.get("sites") or [])
        d = e.extra.get("derived")
        if d:
            it["derived"] = {"function": d.get("function"), "site": d.get("site"),
                             "deps": [k for k in d.get("deps") or [] if not k.startswith("keys:")],
                             "inputs": sorted(d.get("inputs") or {}), "runs": d.get("runs") or []}
        if e.extra.get("alias_of"):
            it["alias_of"] = e.extra["alias_of"]
        return it

    def table(self, key: str) -> dict[str, Any]:
        t = self.idx.tables.get(key)
        if t is None:
            return {"snippet": "\\vouchtable{" + key + "}"}
        rows = []
        for i in range(len(t.rows)):
            row = []
            for col in t.columns:
                ck = t.cell_key(i, col)
                ce = self.idx.get(ck)
                row.append({"key": ck, "value": self.shown(ce) if ce else "",
                            "cited": self.cites.get(ck, [])})
            rows.append(row)
        spec = "".join("l" if (t.row_key == c or (t.rows and isinstance(t.rows[0][j], str)))
                       else "r" for j, c in enumerate(t.columns))
        head = " & ".join(tex_escape(c) for c in t.columns) + " \\\\"
        tabular = ("\\begin{tabular}{" + spec + "}\n  \\toprule\n  " + head + "\n  \\midrule\n"
                   "  \\vouchtable{" + key + "}\n  \\bottomrule\n\\end{tabular}")
        return {"snippet": "\\vouchtable{" + key + "}", "tabular": tabular,
                "columns": list(t.columns), "rows": rows}

    def figure(self, path: str, fig) -> dict[str, Any]:
        rel = path
        if self.paper_dir is not None:
            rel = os.path.relpath(self.cfg.root / path, self.paper_dir).replace(os.sep, "/")
        return {"key": path, "kind": "figure", "value": "", "desc": "figure saved by the run",
                "state": _worst(self.states, [fig.run]), "cited": self.cites.get(path, []),
                "run": fig.run, "site": fig.site or "",
                "snippet": "\\includegraphics[width=\\linewidth]{" + rel + "}"}

    # -- grouping -------------------------------------------------------------------

    def model(self) -> dict[str, Any]:
        scripts: dict[str, dict] = {}

        def group(script: str, fid: str, name: str, how: str, where: str, line: int) -> dict:
            s = scripts.setdefault(script, {"script": script, "runs": [], "functions": {}})
            g = s["functions"].setdefault(fid, {"name": name, "how": how, "where": where,
                                                "line": line, "items": []})
            g["line"] = min(g["line"], line) if line else g["line"]    # where it first appears
            return g

        for key in sorted(self.idx.entries):
            e = self.idx.entries[key]
            if e.kind in ("stat-field", "element", "table-cell"):
                continue
            if e.extra.get("alias_of"):
                g = group(self._values_module(), "aliases", "aliases", "alias", "", 10 ** 9)
                g["items"].append(self.item(e))
                continue
            d = e.extra.get("derived")
            if d:
                fn = str(d.get("function") or "?")
                file, _, qual = fn.partition("::")
                line = _line(d.get("site"))
                group(file or "vouch_values.py", fn, qual or fn, "derived", d.get("site") or "",
                      line)["items"].append(self.item(e))
                continue
            rec = self.idx.runs.get(e.run or "") or {}
            script = rec.get("entry") or f"run {e.run}"
            call = e.extra.get("call")
            if call:
                fn = str(call.get("function") or "?")
                file, _, qual = fn.partition("::")
                how = call.get("via") or "@vouch.track"
                g = group(script, fn, qual, how, e.site or file, _line(e.site))
            elif e.kind == "param":
                g = group(script, f"params:{e.run}", "parameters", "params", "", 10 ** 8)
            else:
                file, qual, line = self.functions.at(e.site)
                top = qual == "<module>"
                g = group(script, f"{file}::{qual}", "top level" if top else qual, "recorded",
                          (file if file != script else "") if top else f"{file}:{line}",
                          _line(e.site) if top else line)
                g.setdefault("lines", [])
                if e.site and e.site not in g["lines"]:
                    g["lines"].append(e.site)
            g["items"].append(self.item(e))

        for path in sorted(self.idx.figures):
            fig = self.idx.figures[path]
            rec = self.idx.runs.get(fig.run) or {}
            script = rec.get("entry") or f"run {fig.run}"
            file, qual, line = self.functions.at(fig.site)
            top = qual == "<module>"
            g = group(script, f"{file}::{qual}", "top level" if top else qual, "recorded",
                      (file if file != script else "") if top else f"{file}:{line}",
                      _line(fig.site) if top else line)
            g["items"].append(self.figure(path, fig))

        for run, rec in sorted(self.idx.runs.items()):
            script = rec.get("entry") or f"run {run}"
            if script not in scripts:
                continue
            st = self.states.get(run)
            git = rec.get("git") or {}
            scripts[script]["runs"].append({
                "id": run, "state": st.state if st else "", "summary": st.summary() if st else "",
                "command": " ".join(rec.get("command") or []),
                "started": str(rec.get("started", "")).replace("T", " ").replace("Z", " UTC"),
                "duration": rec.get("duration_s"), "commit": str(git.get("commit", ""))[:7],
                "dirty": bool(git.get("dirty"))})

        values_module = self._values_module()
        ordered = sorted(scripts.values(), key=lambda s: (s["script"] == values_module, s["script"]))
        for s in ordered:
            s["functions"] = sorted(s["functions"].values(), key=lambda f: (f["line"], f["name"]))
            for f in s["functions"]:
                f.pop("line", None)
        counts = {"values": sum(1 for e in self.idx.entries.values()
                                if e.kind in ("value", "param") and not e.extra.get("alias_of")),
                  "claims": sum(1 for e in self.idx.entries.values() if e.kind == "claim"),
                  "tables": len(self.idx.tables), "figures": len(self.idx.figures),
                  "runs": len(self.idx.runs)}
        issues = [{"check": i.check, "severity": i.severity, "message": i.message}
                  for i in self.issues if i.severity in ("error", "warning")]
        issues += [{"check": p.check, "severity": "warning", "message": p.message}
                   for p in self.idx.problems + self.idx.conflicts()]
        return {"schema": "vouch/1", "project": self.cfg.root.name, "root": self.cfg.root.as_posix(),
                "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "paper_dir": self.cfg.rel(self.paper_dir) if self.paper_dir else None,
                "counts": counts, "issues": issues, "scripts": ordered}

    def _values_module(self) -> str:
        mods = self.cfg.get("python", "values_modules", []) or ["vouch_values.py"]
        return str(mods[0])


def _line(site: str | None) -> int:
    if site and ":" in site:
        tail = site.rpartition(":")[2]
        if tail.isdigit():
            return int(tail)
    return 0


def _field_order(name: str) -> tuple:
    if name in STAT_FIELDS:
        return (0, STAT_FIELDS.index(name), "")
    return (1, int(name) if name.isdigit() else 10 ** 6, name)


def registry(cfg: Config) -> dict[str, Any]:
    """Everything the page shows, as JSON-ready data."""
    return _Builder(cfg).model()


def stamp(cfg: Config) -> str:
    """Changes whenever anything the page shows could have changed."""
    h = hashlib.sha1()
    paths: list[Path] = []
    store = cfg.store
    paths += sorted((store / "runs").glob("*.json")) if (store / "runs").is_dir() else []
    paths += [store / "derived.json", store / "acknowledged.json", store / "accepted.toml",
              cfg.root / "vouch.toml"]
    for d in {Path(cfg.root / p["main"]).parent for p in (cfg.data.get("paper") or [])
              if isinstance(p, dict) and "main" in p}:
        if d.is_dir():
            paths += sorted(d.rglob("*.tex"))
    for p in paths:
        try:
            st = p.stat()
            h.update(f"{p}|{st.st_mtime_ns}|{st.st_size}\n".encode())
        except OSError:
            h.update(f"{p}|-\n".encode())
    return h.hexdigest()[:16]


def page(data: dict | None = None) -> str:
    """The explorer page; with ``data`` embedded it needs no server."""
    html = PAGE.read_text(encoding="utf-8")
    if data is None:
        return html
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return html.replace(DATA_SLOT, blob, 1)


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------

class _State:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.cached: tuple[str, bytes] | None = None

    def registry_json(self) -> bytes:
        with self.lock:
            now = stamp(self.cfg)
            if self.cached is None or self.cached[0] != now:
                try:
                    cfg = Config.load(self.cfg.root)          # vouch.toml may have changed
                    data = registry(cfg)
                except Exception as exc:                      # show it; keep serving
                    data = {"schema": "vouch/1", "error": f"{type(exc).__name__}: {exc}"}
                data["stamp"] = now
                self.cached = (now, json.dumps(data, ensure_ascii=False).encode("utf-8"))
            return self.cached[1]


def make_handler(state: _State, port_ref: list[int]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vouch-explore"

        def _host_ok(self) -> bool:
            # a page on another site must not be able to read this one through DNS rebinding
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"}

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server's naming
            if not self._host_ok():
                self._send(403, b"forbidden", "text/plain")
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._send(200, page().encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/api/registry":
                self._send(200, state.registry_json(), "application/json")
            elif path == "/api/stamp":
                body = json.dumps({"stamp": stamp(state.cfg)}).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, fmt: str, *args) -> None:
            pass
    return Handler


def serve(cfg: Config, port: int = 8765, open_browser: bool = False, out=print) -> None:
    state = _State(cfg)
    port_ref = [port]
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, port_ref))
    except OSError:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, port_ref))
    port_ref[0] = httpd.server_address[1]
    url = f"http://127.0.0.1:{port_ref[0]}/"
    out(f"vouch explore: {url}   (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
