# Project layout & configuration

*Summarizes [SPEC.md §3](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

## Layout

```
vouch.toml                  # configuration (committed)
vouch_values.py             # derive / claim / table / expect definitions (committed, optional)
.vouch/
  runs/<run-id>.json        # run records: one file per run id (committed)
  derived.json              # evaluated derived values, claims and tables, with their hashes (committed)
  acknowledged.json         # change-notification baseline (committed)
  accepted.toml             # reviewed-staleness ledger (committed)
  history.jsonl             # append-only log of detected changes and acks (committed)
  CATALOG.md                # LLM- and human-readable catalog of every value (committed, generated)
  cache/                    # hash cache, derive cache, notification state (gitignored)
paper/
  main.tex
  vouch.sty                 # copied in by `vouch init`, so Overleaf and arXiv need no install (committed)
  vouch-values.tex          # generated (committed)
  vouch-tables/<key>.tex    # generated table bodies (committed)
  vouch-provenance.csv      # only if [[paper]] provenance_csv is set; else `vouch export --csv`
.claude/skills/vouch/SKILL.md   # optional, from `vouch init --agents`
```

Everything except `.vouch/cache/` is committed. Committing is what lets co-authors,
Overleaf, CI and fresh clones check the paper without the artifacts or a GPU.

## Project root discovery

The CLI walks up from the current directory to the nearest `vouch.toml`. The
Python API walks up from the entry script's directory; `VOUCH_ROOT` overrides
both.

If no `vouch.toml` exists, the API falls back to the git top level, warns once
(`no vouch.toml found; recording into <root>/.vouch — run 'vouch init'`), and
records anyway. Adopting vouch should never break an experiment run.

## `vouch.toml` reference

```toml
[[paper]]                                  # one table per paper; several are allowed
main           = "paper/main.tex"          # entry point; \input, \include, \subfile and \import are followed
values_file    = "paper/vouch-values.tex"  # default: next to main
tables_dir     = "paper/vouch-tables"
provenance_csv = "paper/vouch-provenance.csv"   # optional: also write the CSV on every build

[python]
values_modules = ["vouch_values.py"]       # modules defining derive / claim / table / expect
first_party    = []                        # extra roots counted as first-party code (default: the project root)
exclude        = [".venv", "venv", "build", "dist", "node_modules"]

[freshness]
granularity    = "function"                # "function" (needs Python >= 3.12) | "module"
env_drift      = "warn"                    # "ignore" | "warn" | "error"
input_hashing  = "content"                 # "content" | "stat" (size + mtime, for huge datasets)

[inputs]
external       = ["data/raw/"]             # source data a derive may read without a producing run

[[track]]                                  # functions whose results are recorded, no decorator
function       = "experiments/train.py::evaluate"
over           = "seed"

[tables.main]                              # presentation overrides for a recorded or derived table
highlight = { acc = "max" }
midrules  = [2]

[metrics]                                  # project-wide defaults by key glob
"*.acc"  = { fmt = ".1pct", better = "higher", desc = "top-1 test accuracy, {1} on {0}" }
"*.loss" = { fmt = ".3f",   better = "lower",  desc = "test loss, {1} on {0}" }

[format]
rounding       = "half_up"                 # "half_up" | "half_even"
default_float  = ".3g"
siunitx        = false                     # render with \num / \qty
named          = { pct1 = ".1pct", sci2 = ".2e" }

[latex]
tooltip        = "full"                    # "off" | "key" | "value" | "full"
highlight      = "changed"                 # "off" | "changed"
annotate       = false                     # managed "% vouch: ..." trailing comments

[changes]
rel_threshold  = 0.10                      # relative change that makes a change "suspicious"
claim_margin   = 0.01                      # a claim that holds by less than this relative margin is "fragile"
on_change      = []                        # argv of a command that receives new changes as JSON on stdin

[lint]
level          = "warn"                    # "warn" | "error" (--strict forces "error")
allow_years    = true                      # don't flag 19xx/20xx in prose
skip_envs      = ["verbatim", "lstlisting", "minted", "comment"]
allow = [
  { pattern = 'GF\(2\)', why = "the field GF(2), notation not a measurement" },
]

[check]                                    # per-check severity overrides
severity = { "figure-untracked" = "info" }

[hook]
strict         = false                     # pre-commit runs `vouch check` (errors block); true adds --strict
```

Every setting has a working default. `vouch init` writes only `[[paper]] main`
plus commented-out examples.
