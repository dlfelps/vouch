"""``vouch mcp``: the same lookups, for any MCP client (SPEC §13.7).

A Model Context Protocol server over stdio: newline-delimited JSON-RPC 2.0 on
stdin/stdout (logs, if any, go to stderr). It is written against the protocol
directly, with the standard library -- vouch's core has no dependencies, and the
official SDK brings two dozen -- and tested against the official client.

Tools, read-only except ``compare(write=true)``:

    search_values(query, limit)   ranked keys, as ``vouch search``
    get_value(key)                everything known about a key, as ``vouch trace KEY --json``
    cite(key, fmt)                the exact snippet and what it renders as
    compare(a, b, write)          arithmetic, significance, and derive/claim code
    list_pending()                values the paper is owed (``vouch todo``)
    list_changes()                cited values that moved since last acknowledged
    check(strict)                 the gate, as ``vouch check --json``
    trace(target)                 a key, a tex file:line, or a script

Resource ``vouch://catalog``: the catalog, rendered fresh.

Every call reads the project afresh, so the server is stateless and never out of
date. Acknowledging changes and accepting stale runs are deliberately absent:
those stay decisions for a person.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from . import __version__

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")     # newest first

INSTRUCTIONS = (
    "vouch: every number in this paper comes from a recorded experiment run. Never type a "
    "number into LaTeX: find its key with search_values, then paste exactly what cite returns "
    "(\\vouch{key}). For a difference, ratio or 'A beats B', use compare (write=true adds the "
    "definition to vouch_values.py; then the user runs `vouch build`). If a number doesn't exist "
    "yet, don't invent it: tell the user (it can be declared with vouch.expect). Run check "
    "before finishing. Acknowledging changed values is the user's decision, never yours.")

_STR = {"type": "string"}
TOOLS: list[dict] = [
    {"name": "search_values",
     "description": "Find recorded values (keys) by words: key segments, descriptions, the "
                    "function and arguments that produced them. Returns ranked keys with their "
                    "rendered value and the snippet to cite.",
     "inputSchema": {"type": "object", "properties": {
         "query": {**_STR, "description": "words, e.g. 'vit accuracy cifar'"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}},
         "required": ["query"]},
     "annotations": {"title": "Search values", "readOnlyHint": True}},
    {"name": "get_value",
     "description": "Everything known about one key: rendered and raw value, description, the "
                    "call or definition that produced it, its runs and their freshness, where "
                    "the paper cites it, and any pending change.",
     "inputSchema": {"type": "object", "properties": {"key": _STR}, "required": ["key"]},
     "annotations": {"title": "Get value", "readOnlyHint": True}},
    {"name": "cite",
     "description": "The exact LaTeX to cite a key (\\vouch{key}, \\vouchclaim, \\vouchtable) and "
                    "what it renders as; optionally with another format such as '.2pct'.",
     "inputSchema": {"type": "object", "properties": {"key": _STR, "fmt": _STR},
                     "required": ["key"]},
     "annotations": {"title": "Cite", "readOnlyHint": True}},
    {"name": "compare",
     "description": "Compare two numeric values: difference, ratio, relative change, which is "
                    "better, and for two mean±std results a Welch t-test p-value. Returns the "
                    "@vouch.derive and @vouch.claim code that makes the comparison citable; "
                    "write=true appends it to vouch_values.py.",
     "inputSchema": {"type": "object", "properties": {
         "a": _STR, "b": _STR, "write": {"type": "boolean", "default": False}},
         "required": ["a", "b"]},
     "annotations": {"title": "Compare", "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": True}},
    {"name": "list_pending",
     "description": "Values the paper cites that no run has recorded yet (declared with "
                    "vouch.expect), with the command that would produce each.",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"title": "List pending", "readOnlyHint": True}},
    {"name": "list_changes",
     "description": "Cited values that changed since someone last read the sentences around "
                    "them, with those sentences. Re-read each; report SUSPICIOUS ones to the user.",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"title": "List changes", "readOnlyHint": True}},
    {"name": "diff_values",
     "description": "What moved between a git revision and the working tree (or two "
                    "revisions): every recorded value added, removed or changed, with the delta, "
                    "its direction, and better/worse where the key says which way is better. Use "
                    "it after a re-run to say what the re-run changed.",
     "inputSchema": {"type": "object", "properties": {
         "rev": {**_STR, "default": "HEAD"},
         "to": {**_STR, "description": "a second revision; omit for the working tree"},
         "cited": {"type": "boolean", "default": False, "description": "only cited keys"}}},
     "annotations": {"title": "Diff values", "readOnlyHint": True}},
    {"name": "check",
     "description": "The gate: every cited key exists and is fresh, claims hold, generated files "
                    "are current, no typed numbers. Issues come in the order to fix them, each "
                    "with a fix.",
     "inputSchema": {"type": "object", "properties": {
         "strict": {"type": "boolean", "default": True}}},
     "annotations": {"title": "Check", "readOnlyHint": True}},
    {"name": "trace",
     "description": "Where something came from: a key (its full provenance), a tex 'file:line' "
                    "(the values cited there), or a script path (the runs that used it).",
     "inputSchema": {"type": "object", "properties": {"target": _STR}, "required": ["target"]},
     "annotations": {"title": "Trace", "readOnlyHint": True}},
]
RESOURCES = [{"uri": "vouch://catalog", "name": "catalog", "title": "vouch catalog",
              "description": "Every citable key, one line each (.vouch/CATALOG.md, rendered fresh).",
              "mimeType": "text/markdown"}]


class ToolError(Exception):
    """A tool ran but could not answer (unknown key, bad arguments): reported to the model."""


class Server:
    def __init__(self, root: Path | None = None):
        self.root = root

    # -- the project, read afresh for every call ----------------------------------

    def _cfg(self):
        from .config import Config, discover_root
        root, how = discover_root(self.root) if self.root else discover_root(Path.cwd())
        if how not in ("config", "env"):
            raise ToolError(f"no vouch.toml found from {root}; run `vouch init` in the project")
        return Config.load(root)

    def _ctx(self):
        from .build import plan
        return plan(self._cfg(), need_paper=False, check_env=False)

    # -- tools ----------------------------------------------------------------------

    def tool_search_values(self, query: str, limit: int = 10) -> dict:
        from .assist import search
        return {"query": query, "results": search(self._ctx(), query, limit=int(limit))}

    def tool_get_value(self, key: str) -> dict:
        from .assist import describe
        got = describe(self._ctx(), key)
        if "error" in got:
            raise ToolError(got["error"] + (f"; did you mean {', '.join(got['suggestions'])}?"
                                             if got.get("suggestions") else ""))
        return got

    def tool_cite(self, key: str, fmt: str | None = None) -> dict:
        from .assist import cite, shown, snippet
        from .render import Options
        ctx = self._ctx()
        got = cite(ctx, key)
        if "error" in got:
            raise ToolError(got["error"] + (f"; did you mean {', '.join(got['suggestions'])}?"
                                             if got.get("suggestions") else ""))
        if fmt and got.get("kind") not in ("claim", "table", "pending"):
            e = ctx.idx.get(key)
            got["snippets"] = [{"latex": snippet(e, fmt),
                                "renders": shown(e, Options.from_config(ctx.cfg), fmt), "fmt": fmt}]
        return got

    def tool_compare(self, a: str, b: str, write: bool = False) -> dict:
        from .assist import compare, write_definitions
        ctx = self._ctx()
        got = compare(ctx, a, b)
        if "error" in got:
            raise ToolError(got["error"])
        if write:
            path, clash = write_definitions(ctx.cfg, got["code"], [got["derive_key"], got["claim_key"]])
            got["written"] = None if clash else ctx.cfg.rel(path)
            got["already_defined"] = clash
            got["next"] = "run `vouch build`, then cite the derived key" if not clash else None
        return got

    def tool_list_pending(self) -> dict:
        from .assist import todo
        return {"owed": todo(self._ctx())}

    def tool_list_changes(self) -> dict:
        ctx = self._ctx()
        return {"changes": [c.to_json() for c in ctx.changes if c.pending]}

    def tool_diff_values(self, rev: str = "HEAD", to: str | None = None,
                         cited: bool = False) -> dict:
        from .vdiff import DiffError, diff
        try:
            return diff(self._cfg(), rev, to, cited_only=bool(cited)).to_json()
        except DiffError as exc:
            raise ToolError(str(exc)) from None

    def tool_check(self, strict: bool = True) -> dict:
        from .check import run_check
        rep = run_check(self._cfg(), strict=bool(strict), check_env=False)
        return {"ok": rep.ok, "summary": {**rep.summary, "errors": len(rep.errors),
                                          "warnings": len(rep.warnings)},
                "issues": [i.to_json() for i in rep.issues if i.severity != "info"]}

    def tool_trace(self, target: str) -> dict:
        from .assist import trace
        got = trace(self._ctx(), target)
        if "error" in got:
            raise ToolError(got["error"])
        return got

    # -- resources --------------------------------------------------------------------

    def read_resource(self, uri: str) -> dict:
        if uri != "vouch://catalog":
            raise KeyError(uri)
        from .catalog import render_catalog
        ctx = self._ctx()
        text = render_catalog(ctx)[ctx.cfg.store / "CATALOG.md"]
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}

    # -- JSON-RPC ---------------------------------------------------------------------

    def handle(self, msg: Any) -> dict | list | None:
        """The response to one message (or batch); None for notifications."""
        if isinstance(msg, list):
            out = [r for r in (self.handle(m) for m in msg) if r is not None]
            return out or None
        if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
            return None                        # a response to nothing we asked: ignore it
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        is_request = "id" in msg
        try:
            result = self._dispatch(method, params)
        except _RpcError as exc:
            return _error(mid, exc.code, exc.message) if is_request else None
        except Exception as exc:                                  # a bug: say so, keep serving
            traceback.print_exc(file=sys.stderr)
            return _error(mid, -32603, f"internal error: {exc!r}") if is_request else None
        if not is_request:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def _dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {"protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                    "capabilities": {"tools": {"listChanged": False},
                                     "resources": {"subscribe": False, "listChanged": False}},
                    "serverInfo": {"name": "vouch", "title": "vouch", "version": __version__},
                    "instructions": INSTRUCTIONS}
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call(params.get("name"), params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": RESOURCES}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            try:
                return self.read_resource(str(params.get("uri")))
            except KeyError:
                raise _RpcError(-32002, f"resource not found: {params.get('uri')}") from None
        if method in ("prompts/list",):
            return {"prompts": []}
        if method == "logging/setLevel":
            return {}
        raise _RpcError(-32601, f"method not found: {method}")

    def _call(self, name: Any, args: dict) -> dict:
        fn: Callable | None = getattr(self, f"tool_{name}", None) if isinstance(name, str) else None
        if fn is None:
            raise _RpcError(-32602, f"unknown tool: {name}")
        spec = next(t for t in TOOLS if t["name"] == name)
        allowed = set(spec["inputSchema"]["properties"])
        missing = [k for k in spec["inputSchema"].get("required", []) if k not in args]
        extra = sorted(set(args) - allowed)
        if missing or extra:
            return _tool_result({"error": f"{name}: " + "; ".join(
                ([f"missing {missing}"] if missing else []) + ([f"unknown {extra}"] if extra else []))},
                error=True)
        try:
            return _tool_result(fn(**args))
        except ToolError as exc:
            return _tool_result({"error": str(exc)}, error=True)


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tool_result(data: dict, error: bool = False) -> dict:
    text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    out = {"content": [{"type": "text", "text": text}], "isError": error}
    if not error:
        out["structuredContent"] = json.loads(text)
    return out


def serve(root: Path | None = None, stdin=None, stdout=None) -> int:
    """Answer JSON-RPC messages, one per line, until stdin closes."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for stream in (stdin, stdout):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError):
            pass
    server = Server(root)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply: Any = _error(None, -32700, "Parse error")
        else:
            reply = server.handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
