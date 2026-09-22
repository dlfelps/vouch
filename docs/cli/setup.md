# Setup: init, build, watch, hook

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

## `vouch watch`

Keep `vouch build` running for you. `vouch watch` builds once, then rebuilds
whenever something it depends on changes, and after each rebuild prints the
values that moved.

```console
$ vouch watch [--interval SEC] [--then CMD] [--no-notify] [--quiet] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `--interval SEC` | seconds between polls (default `1`) |
| `--then CMD` | run `CMD` after each build that wrote files, e.g. `"latexmk -pdf paper/main.tex"` |
| `--no-notify` | don't run the `on_change` hook |
| `--quiet` | one line per build (plus errors and moved values) instead of the full build report |

What triggers a rebuild:

- a run record appears or changes in `.vouch/runs/` (an experiment finished, or `vouch run`/`vouch import` recorded values)
- `vouch.toml`
- the values modules (`vouch_values.py`)
- any `.tex` file of any paper, including files `\input` later

The files the build writes (values file, tables, CSV, `.vouch/derived.json`,
the catalog) never trigger a rebuild. The watcher polls file timestamps with
the standard library, so it behaves the same on every OS and on network
filesystems. It **never runs an experiment**; it only re-renders what was
recorded. When a build fails (a half-written `vouch_values.py`, a typo in
`vouch.toml`), it prints the error and keeps watching.

```console
$ vouch watch --quiet --then "latexmk -pdf -cd paper/main.tex"
[18:28:19] vouch watch: building, then watching for new values (Ctrl-C to stop)
  wrote 3 file(s)

[18:28:24] .vouch/runs/train.json changed
  4 value(s) moved since the last build:
    run train
        ● toy.centroid.acc      79.3 ± 3.1% → 77.8 ± 3.8%   ↓ Δ -1.56 pts, -2.0% relative   worse
        ● toy.centroid.n_test   50 → 120   ↑ Δ +70, +140.0%   ! large move (140%)
    derived
        ● toy.gain              29.3 → 28.9   ↓ Δ -0.4444, -1.5%   worse
    claims
        ● toy.gain_over_10      HOLDS → HOLDS   ! margin 1.93 → 1.89
  wrote 3 file(s); 4 pending change(s): `vouch review`
  ✓ latexmk -pdf -cd paper/main.tex
```

Values marked `●` are cited in the paper. The line format is the same as
[`vouch diff`](check.md#vouch-diff).

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
