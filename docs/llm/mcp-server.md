# MCP server

*Summarizes [SPEC.md §13.6–13.7](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

## JSON output (`schema: vouch/1`)

Every command's `--json` output uses the same envelope:

```json
{
  "schema": "vouch/1",
  "command": "check",
  "ok": false,
  "summary": {"errors": 2, "warnings": 1, "values": 38, "runs": 6},
  "issues": [
    {"check": "stale", "severity": "error", "subject": "run:cifar_vit",
     "message": "src/models/vit.py::ViT.forward changed since the run",
     "where": [{"file": "paper/main.tex", "line": 118, "key": "cifar.vit.acc"}],
     "fix": {"kind": "command", "value": "python experiments/train.py --model vit"},
     "detail": {"units": ["src/models/vit.py::ViT.forward"]}}
  ]
}
```

- **Issue order** is the order to fix them in, so an agent can loop on
  `vouch check --strict --json` until it exits 0: config → `store-edited` →
  `unknown-key` → `out-of-sync` → stale runs → false claims → changes →
  pending → lint.
- **Fix kinds:** `command` (run this); `edit` (file, line, old text → new
  text); `build` (run `vouch build`); `human` (needs the user: ack, accept, or
  a judgement).
- **Compatibility:** field names are stable within `vouch/1`; additions are
  allowed, removals need `vouch/2`. The schema ships as
  [`vouch/schema/v1.json`](https://github.com/dlfelps/vouch/blob/main/src/vouch/schema/v1.json).

## `vouch mcp`

`vouch mcp` exposes the same functions to any MCP client over stdio. The tools
are read-only, except that `compare` may write the definition when asked:

| Tool | Returns |
|---|---|
| `search_values(query, limit=10)` | ranked keys, as in `vouch search` |
| `get_value(key)` | the full record, as in `vouch trace --json` |
| `cite(key, fmt=None)` | the snippet and its rendering |
| `compare(a, b, write=False)` | arithmetic plus derive/claim code |
| `list_pending()` | `vouch todo` |
| `list_changes()` | `vouch changes` |
| `check(strict=True)` | the `vouch/1` envelope |
| `trace(target)` | `vouch trace` |

Resource: `vouch://catalog` (the catalog, rendered fresh). `ack` and `accept`
are deliberately **not** exposed: acknowledgment and acceptance stay human
actions.

**Built on the protocol, not the SDK.** MCP over stdio is newline-delimited
JSON-RPC 2.0, and the server implements it with the standard library in about
300 lines, so `vouch mcp` works out of the box with no extra install — no
pydantic, starlette or uvicorn pulled into a tool whose core has none.

**What it speaks:**

- Handshake revisions 2025-06-18, 2025-03-26 and 2024-11-05.
- Methods: `initialize`, `ping`, `tools/list`, `tools/call`, `resources/list`, `resources/read`, `resources/templates/list`, and notifications. Batches are accepted.
- Newer clients that first probe `server/discover` fall back to the handshake, since that method isn't found here.

**Behaviour:**

- **Stateless:** every call reads the project afresh, so answers are never out of date.
- **Tool results:** a failure the model should see (an unknown key, a missing argument, no `vouch.toml`) is a tool result with `isError: true` and a message, not a protocol error.
- **Logs** go to stderr only.

**Registering it:** `vouch init --agents mcp` writes `.mcp.json`:

```json
{"mcpServers": {"vouch": {"command": "vouch", "args": ["mcp"]}}}
```

It is not part of the default `--agents` set, because the skill and hook
already cover Claude Code.
