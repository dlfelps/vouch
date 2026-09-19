# Setup: init, build, hook

## `vouch init`

Set up `vouch.toml`, the store and `vouch.sty`.

```console
$ vouch init [--paper FILE] [--hook] [--agents [PARTS]] [--stop-gate] [--yes] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `--paper FILE` | the paper's main `.tex` (default: detected) |
| `--hook` | also install the git pre-commit hook |
| `--agents [PARTS]` | set up Claude Code: `skill,rules,hook,mcp` (default when the flag is bare: `skill,rules,hook`) |
| `--stop-gate` | with `--agents`: also block finishing until `vouch check --strict` passes |
| `--yes` | write without asking |

```console
$ vouch init
  wrote vouch.toml (paper: paper/main.tex)
  copied vouch.sty to paper/vouch.sty
  add to the preamble of paper/main.tex:  \usepackage{vouch}
  marked generated files in .gitattributes
```

Writes `vouch.toml`, detects the main `.tex`, copies `vouch.sty`, adds
`.gitignore` entries (`.vouch/cache/`), and prints the `\usepackage` line to
add. See [Claude Code](../llm/claude-code.md) for what `--agents` installs.

## `vouch build`

Evaluate `vouch_values.py`, render the values file, tables and provenance CSV
(if `provenance_csv` is set) and catalog, refresh source annotations if
enabled, auto-acknowledge new/`hidden`/`reformatted` changes, and print the
change block.

```console
$ vouch build [--json] [--no-notify] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `--json` | machine-readable summary |
| `--no-notify` | don't run the `on_change` hook |

```console
$ vouch build
vouch build: evaluated vouch_values.py (6 definitions) -> .vouch/derived.json
vouch build: paper/main.tex
  wrote: paper/vouch-values.tex (284 values, 2 claims, 1 tables; 22 citations)
```

## `vouch hook`

Git pre-commit hook (`install`), or the Claude Code hook entry points
(`claude`, `stop`).

```console
$ vouch hook install [--strict] [--force]
$ vouch hook claude          # reads a Claude Code PostToolUse payload from stdin
$ vouch hook stop            # reads a Claude Code Stop payload from stdin
```

| Flag | Meaning |
|---|---|
| `--strict` | the hook runs `check --strict` |
| `--force` | replace an existing pre-commit hook |

```console
$ vouch hook install
installed .git/hooks/pre-commit: runs `vouch check --quiet` before each commit
```

`vouch hook claude` and `vouch hook stop` are the entry points `vouch init --agents`
wires into `.claude/settings.json`; see [Claude Code](../llm/claude-code.md).
