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
- Give the function itself a real docstring, too: vouch reads it fresh from the
  source and shows it back at every lookup (`vouch cite`, `vouch search`,
  `vouch explore`) as a sanity check that a key means what it looks like it means.
- A single number: `vouch.record(key, value, desc=...)`; a dict: `vouch.record_all(d, prefix=...)`.
- Multi-seed results: `over="seed"` or `vouch.Stat.of(per_seed)` -- never a hand-computed mean.
- A long sweep: one `with vouch.run(f"sweep_n{n}", params={"n": n}):` per configuration,
  so finished configurations stay recorded.
- A slow part beside quick ones: its own run, its body in a function, guarded with
  `if not vouch.fresh("slow_id"):` so that editing a quick part does not re-run it.
  A figure of the slow run's numbers is a quick run that reads them with
  `vouch.value(key)`, never through a side file of copied numbers.
- A property the experiment checks: `vouch.claim(key, ok, desc=..., values={...})`
  where it is checked, with the numbers behind it.
- A table: `vouch.table(key, rows, columns=[...], row_key="id", desc=...)`; every cell
  is a key too, `<key>.<row>.<col>`.
- Data read: `vouch.input(path)`; files written: `vouch.artifact(path)`; figures saved
  with matplotlib inside the run are tracked automatically.
- Run the script normally. `vouch status` lists stale runs with their re-run command.

## Write a results paragraph

1. Find each number: `vouch search "vit accuracy cifar"` or the catalog.
2. Paste exactly what `vouch cite KEY` prints (`\vouch{...}`, or `\vouch[.2pct]{...}`
   for another precision; `.mean`, `.std`, `.n` for parts of a mean ± std).
   `vouch cite` also prints the producing function's docstring as `context:` when
   it has one -- if the user already documented it, use their own wording instead
   of guessing at a description. Formats that read like prose: `[w]` ("two qubits",
   zero to ten as words), `[,d]` (37,920), `[.0pct]`, `[.2fx]` (1.90x). A whole table
   is `\vouchtable{key}`; a hand-written tabular cites its cells.
3. Differences, ratios, shares, "2x": never compute them. `vouch compare A B --write`
   adds a `@vouch.derive` and a `@vouch.claim` to `vouch_values.py`; then `vouch build`
   and cite the derived key. A definition can read a file (`inputs=[path]`) or a
   table (`v["table"]` is its rows as dicts), so a result held to a published one,
   or "in every case", is a claim too. Add the definition before citing it: the
   edit hook reports a key nothing defines yet as unknown.
4. "Outperforms", "in every seed", "significantly": back them with a claim and wrap
   the prose: `\vouchclaim{key}{ResNet outperforms ViT}`. Write "significantly" only
   if `vouch compare` reports p < 0.05.
5. A number no run has produced yet: cite `\vouch{new.key}` anyway. If a script
   already records it by that literal name (`vouch.record("new.key", ...)`, a line
   just added or a run still going), that is enough: the key is pending, with the
   script as its producer. Otherwise add `vouch.expect("new.key", desc=...,
   producer="python ...")` to `vouch_values.py`. Either way, tell the user the
   experiment is owed (`vouch todo`). Never invent it.
6. `vouch build`, then `vouch check` must pass. Before the paper is submitted,
   `vouch check --strict` must pass too. It also fails on pending `expect()` values,
   which step 5 allows while experiments are owed, so don't hold a draft to it.

## Handle changed values

`vouch changes` lists cited values that moved since someone last read their prose,
with each citing sentence. Re-read every sentence; fix any the new value makes
wrong ("the best", "roughly doubles"); report SUSPICIOUS changes to the user (they
may be bugs). Only the user acknowledges: never run `vouch ack` or `vouch accept`
yourself.

After the user re-runs an experiment, `vouch diff` says what the re-run did to every
recorded value (cited or not) since the last commit: old -> new, delta, direction,
better/worse. Use it to summarize a re-run; don't guess from memory.

When an experiment is redefined rather than re-run, give its keys new names: a key
that keeps its name but changes meaning shows up as a suspicious move against a
baseline that no longer applies.

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
| `vouch check [--strict] [--json]` | the gate (`--strict` at submission); `--json` lists issues in the order to fix them |
| `vouch changes` | cited values that moved (the user acks, not you) |
| `vouch diff [REV]` | every recorded value that moved since a commit (default HEAD) |
| `vouch trace KEY` | where a value came from |
| `vouch explore` | browse everything in a web page |
| `vouch document [SCRIPT]` | a real-values summary of a script -- docstrings plus what it actually measured, for sharing or revisiting later; needs no paper |

Never edit `.vouch/` or the generated `vouch-values.tex` / `vouch-tables/` by hand.
"""

RULES = """<!-- vouch -->
## Numbers in the paper (vouch)
- Never type an empirical number into LaTeX. Find it (`vouch search`, `.vouch/CATALOG.md`), then paste what `vouch cite KEY` prints.
- If it doesn't exist: record it in the experiment, `@vouch.derive` it, or `vouch.expect()` it and tell the user it is owed.
- Give a new tracked function a real docstring -- vouch shows it back at lookup time (`vouch cite`, `vouch search`) as a sanity check. If one already exists on a function you're writing about, use it instead of guessing a description.
- Never compute with numbers in prose (differences, ratios, "2x"): `vouch compare A B --write`, then cite the derived key.
- Qualitative comparisons ("outperforms", "all seeds") go in `\\vouchclaim` backed by a claim.
- Before finishing: `vouch build` and `vouch check` must pass. Before submitting: `vouch check --strict`, which also fails on pending `vouch.expect()` values -- allowed while results are owed, never replaced by invented numbers.
- If `vouch changes` lists anything: re-read each cited sentence, fix wrong text, and report SUSPICIOUS changes to the user.
- Never run `vouch ack` or `vouch accept` without the user's approval. Never edit `.vouch/` or generated files.
<!-- /vouch -->
"""


def _uv_project_has_vouch(root: Path) -> bool:
    """A uv project whose lock file installs vouch: ``uv run`` will find it."""
    lock = root / "uv.lock"
    try:
        return '\nname = "vouch-paper"\n' in lock.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError:
        return False


def _vouch_launcher(root: Path | None) -> tuple[list[str], bool]:
    """(argv that starts ``vouch``, portable) for a hook or server the agent will run.

    The agent runs it later, from its own shell, without the environment ``vouch init``
    ran in. ``shutil.which("vouch")`` alone was therefore the wrong test: under
    ``uv run vouch init`` it finds the project's virtual environment, and the hook it
    wrote, a bare ``vouch``, then failed on every edit. In order of preference:

    * a uv project whose lock installs vouch: ``uv run --no-sync vouch``, which works
      for everyone who checks the project out;
    * vouch in a virtual environment inside the project: its path from
      ``$CLAUDE_PROJECT_DIR``, also portable across checkouts;
    * vouch on PATH outside any virtual environment: ``vouch``;
    * otherwise this interpreter by absolute path, which is machine-specific and so
      goes to ``settings.local.json``.
    """
    if root is not None and shutil.which("uv") and _uv_project_has_vouch(root):
        return ["uv", "run", "--no-sync", "vouch"], True
    found = shutil.which("vouch")
    if found and not Path(found).parent.parts:          # a bare name: on PATH as such
        return ["vouch"], True
    if found:
        where = Path(found).resolve()
        if root is not None:
            try:
                rel = where.relative_to(Path(root).resolve()).as_posix()
                return [f'"$CLAUDE_PROJECT_DIR"/{rel}'], True
            except ValueError:
                pass
        in_venv = sys.prefix != sys.base_prefix
        if not (in_venv and Path(sys.prefix).resolve() in where.parents):
            return ["vouch"], True
    return [f'"{sys.executable}"', "-m", "vouch"], False


def _hook_command(sub: str, root: Path | None = None) -> tuple[str, bool]:
    """(command, portable) that runs ``vouch hook <sub>`` from the agent's shell."""
    argv, portable = _vouch_launcher(root)
    return " ".join([*argv, "hook", sub]), portable


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
        cmd, portable = _hook_command("claude", root)
        p = root / ".claude" / ("settings.json" if portable else "settings.local.json")
        text = old(p)
        try:
            settings = json.loads(text) if text.strip() else {}
        except ValueError:
            settings = None
        if settings is not None:
            settings = _with_hook(settings, "PostToolUse", "Edit|Write|MultiEdit", cmd)
            if stop_gate:
                settings = _with_hook(settings, "Stop", None, _hook_command("stop", root)[0])
            out[p] = (text, json.dumps(settings, indent=2) + "\n")
    if "mcp" in parts:
        p = root / ".mcp.json"
        text = old(p)
        try:
            cfg = json.loads(text) if text.strip() else {}
        except ValueError:
            cfg = None
        if cfg is not None:
            argv, portable = _vouch_launcher(root)
            if not portable:
                argv = [sys.executable, "-m", "vouch"]
            elif argv[0].startswith('"$CLAUDE_PROJECT_DIR"/'):
                # an MCP client starts the server without a shell: no variable expansion,
                # and the working directory is the project root
                argv = [argv[0].split("/", 1)[1]]
            entry = {"command": argv[0], "args": [*argv[1:], "mcp"]}
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
