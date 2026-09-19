"""``vouch init --agents``: teach Claude Code (or any agent) to use vouch (SPEC §13.5).

Three optional parts, each shown as a diff before anything is written:

* **skill** -- ``.claude/skills/vouch/SKILL.md``: workflows for recording results,
  writing a results paragraph, handling changed values and converting a paper. It
  loads only when relevant, so it costs no context otherwise.
* **rules** -- a short block in ``CLAUDE.md`` (or ``AGENTS.md``) between
  ``<!-- vouch -->`` markers, updated in place.
* **hook** -- a PostToolUse hook in ``.claude/settings.json`` running ``vouch hook
  claude`` after every edit, so a typed number or a mistyped key is caught in the
  same turn. ``--stop-gate`` adds a Stop hook running ``vouch check --strict``.
* **mcp** (only when asked for) -- ``.mcp.json`` registering ``vouch mcp``, the same
  lookups as tools for any MCP client.
"""

from __future__ import annotations

import difflib
import json
import shutil
import sys
from pathlib import Path

PARTS = ("skill", "rules", "hook")          # the default set
OPTIONAL = ("mcp",)                           # .mcp.json: the MCP server, for any MCP client
MARK_START, MARK_END = "<!-- vouch -->", "<!-- /vouch -->"

SKILL = r"""---
name: vouch
description: Use when writing or editing LaTeX in a project that has vouch.toml (a paper whose numbers come from experiments), when writing experiment code whose results go into a paper, or when the user mentions results, numbers, tables, claims, figures or vouch. Every number is cited from a recorded run, never typed.
---

# vouch: every number in the paper comes from a run

The paper never contains a typed number. Experiments record values; the paper
cites them as `\vouch{key}`; `vouch check` proves each one exists and is fresh.
Numbers computed from other numbers live in `vouch_values.py`. A number that
doesn't exist yet is a placeholder (`vouch.expect`), never a guess.

Start by reading `.vouch/CATALOG.md`: every citable key, one per line.

## Record results in an experiment

Decorate the function that computes the result; every call is recorded, keyed by
the function and its arguments:

```python
import vouch

@vouch.track(over="seed")                     # seeds -> mean ± std, per call results kept
def evaluate(dataset: str, model: str, seed: int = 0) -> dict:
    ...
    return {"acc": acc, "loss": loss}           # -> evaluate.<dataset>.<model>.acc / .loss / .time
```

- Descriptions and formats for families of keys go in `vouch.toml` `[metrics]`
  (`"*.acc" = { fmt = ".1pct", better = "higher", desc = "top-1 test accuracy" }`),
  not repeated in code. Every new metric needs a desc and `better=`.
- A single number: `vouch.record(key, value, desc=...)`; a dict: `vouch.record_all(d, prefix=...)`.
- Multi-seed results: `over="seed"` or `vouch.Stat.of(per_seed)` -- never a hand-computed mean.
- Data read: `vouch.input(path)`; files written: `vouch.artifact(path)`; figures saved
  with matplotlib inside the run are tracked automatically.
- Run the script normally. `vouch status` lists stale runs with their re-run command.

## Write a results paragraph

1. Find each number: `vouch search "vit accuracy cifar"` or the catalog.
2. Paste exactly what `vouch cite KEY` prints (`\vouch{...}`, or `\vouch[.2pct]{...}`
   for another precision; `.mean`, `.std`, `.n` for parts of a mean ± std).
3. Differences, ratios, "2x": never compute them. `vouch compare A B --write` adds a
   `@vouch.derive` and a `@vouch.claim` to `vouch_values.py`; then `vouch build` and
   cite the derived key.
4. "Outperforms", "in every seed", "significantly": back them with a claim and wrap
   the prose: `\vouchclaim{key}{ResNet outperforms ViT}`. Write "significantly" only
   if `vouch compare` reports p < 0.05.
5. A number no run has produced yet: cite `\vouch{new.key}` anyway, add
   `vouch.expect("new.key", desc=..., producer="python ...")` to `vouch_values.py`,
   and tell the user the experiment is owed (`vouch todo`). Never invent it.
6. `vouch build`, then `vouch check --strict` must pass.

## Handle changed values

`vouch changes` lists cited values that moved since someone last read their prose,
with each citing sentence. Re-read every sentence; fix any the new value makes
wrong ("the best", "roughly doubles"); report SUSPICIOUS changes to the user (they
may be bugs). Only the user acknowledges: never run `vouch ack` or `vouch accept`
yourself.

## Convert an existing paper

`vouch suggest` classifies every typed number: replaceable (exactly one recorded
value prints like it), ambiguous, or NO SOURCE (nothing recorded prints like it).
`vouch suggest --apply` rewrites only the replaceable ones. Show the user every NO
SOURCE number: each needs a run that records it, or removal.

## Cheat sheet

| | |
|---|---|
| `vouch search WORDS` / `vouch cite KEY` | find a key / the exact snippet |
| `vouch compare A B [--write]` | arithmetic, significance, derive + claim code |
| `vouch todo` | values the paper cites that no run recorded yet |
| `vouch build` | evaluate `vouch_values.py`, regenerate the LaTeX and the catalog |
| `vouch check --strict [--json]` | the gate; `--json` lists issues in the order to fix them |
| `vouch changes` | cited values that moved (the user acks, not you) |
| `vouch trace KEY` | where a value came from |
| `vouch explore` | browse everything in a web page |

Never edit `.vouch/` or the generated `vouch-values.tex` / `vouch-tables/` by hand.
"""

RULES = """<!-- vouch -->
## Numbers in the paper (vouch)
- Never type an empirical number into LaTeX. Find it (`vouch search`, `.vouch/CATALOG.md`), then paste what `vouch cite KEY` prints.
- If it doesn't exist: record it in the experiment, `@vouch.derive` it, or `vouch.expect()` it and tell the user it is owed.
- Never compute with numbers in prose (differences, ratios, "2x"): `vouch compare A B --write`, then cite the derived key.
- Qualitative comparisons ("outperforms", "all seeds") go in `\\vouchclaim` backed by a claim.
- Before finishing: `vouch build` and `vouch check --strict` must pass.
- If `vouch changes` lists anything: re-read each cited sentence, fix wrong text, and report SUSPICIOUS changes to the user.
- Never run `vouch ack` or `vouch accept` without the user's approval. Never edit `.vouch/` or generated files.
<!-- /vouch -->
"""


def _hook_command(sub: str) -> tuple[str, bool]:
    """(command, portable): ``vouch hook ...`` if vouch is on PATH, else this Python."""
    if shutil.which("vouch"):
        return f"vouch hook {sub}", True
    return f'"{sys.executable}" -m vouch hook {sub}', False


def _with_hook(settings: dict, event: str, matcher: str | None, command: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    groups = hooks.setdefault(event, [])
    for g in groups:
        if any(h.get("command") == command for h in g.get("hooks", [])):
            return settings
    group: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher:
        group = {"matcher": matcher, **group}
    groups.append(group)
    return settings


def planned(root: Path, parts: list[str], stop_gate: bool = False) -> dict[Path, tuple[str, str]]:
    """{path: (old text, new text)} for the files ``init --agents`` would write."""
    out: dict[Path, tuple[str, str]] = {}

    def old(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""
    if "skill" in parts:
        p = root / ".claude" / "skills" / "vouch" / "SKILL.md"
        out[p] = (old(p), SKILL)
    if "rules" in parts:
        p = root / "CLAUDE.md"
        if not p.exists() and (root / "AGENTS.md").exists():
            p = root / "AGENTS.md"
        text = old(p)
        if MARK_START in text and MARK_END in text:
            a, rest = text.split(MARK_START, 1)
            _, b = rest.split(MARK_END, 1)
            new = a + RULES.rstrip("\n") + b
        else:
            new = (text.rstrip("\n") + "\n\n" if text.strip() else "") + RULES
        out[p] = (text, new)
    if "hook" in parts:
        cmd, portable = _hook_command("claude")
        p = root / ".claude" / ("settings.json" if portable else "settings.local.json")
        text = old(p)
        try:
            settings = json.loads(text) if text.strip() else {}
        except ValueError:
            settings = None
        if settings is not None:
            settings = _with_hook(settings, "PostToolUse", "Edit|Write|MultiEdit", cmd)
            if stop_gate:
                settings = _with_hook(settings, "Stop", None, _hook_command("stop")[0])
            out[p] = (text, json.dumps(settings, indent=2) + "\n")
    if "mcp" in parts:
        p = root / ".mcp.json"
        text = old(p)
        try:
            cfg = json.loads(text) if text.strip() else {}
        except ValueError:
            cfg = None
        if cfg is not None:
            cmd, portable = _hook_command("")
            entry = ({"command": "vouch", "args": ["mcp"]} if portable else
                     {"command": sys.executable, "args": ["-m", "vouch", "mcp"]})
            servers = cfg.setdefault("mcpServers", {})
            if servers.get("vouch") != entry:
                servers["vouch"] = entry
                out[p] = (text, json.dumps(cfg, indent=2) + "\n")
    return {p: (a, b) for p, (a, b) in out.items() if a != b}


def diff(root: Path, changes: dict[Path, tuple[str, str]]) -> str:
    chunks = []
    for p, (a, b) in changes.items():
        rel = p.relative_to(root).as_posix()
        chunks += difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                       fromfile=f"a/{rel}" if a else "/dev/null",
                                       tofile=f"b/{rel}")
    return "".join(chunks)


def write(changes: dict[Path, tuple[str, str]]) -> None:
    for p, (_, text) in changes.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
