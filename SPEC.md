# vouch — Specification

**Status:** draft v0.1 for review · **Date:** 2026-09-18 · **Author:** Daniel Felps

> Every number in the paper is vouched for by the code that produced it.

vouch links the numbers in a LaTeX paper to the experiment runs that produced them. Experiments **record** values while they run. The paper **cites** those values by key, and each value is inserted from a generated file, so no number is ever typed by hand. A fast **check** confirms that every cited value exists, that the code behind it hasn't changed since the run, that stated claims still hold, and that figures are current. When a cited value changes, vouch **notifies** you until someone has re-read the sentences that cite it. Any number can be **traced** back to the file and line, run, command, commit and inputs that produced it, from the terminal, a CSV, or by hovering over it in the PDF.

vouch is designed for two kinds of user: a researcher, and the **LLM coding agent** working alongside them. The agent writes experiment code, drafts LaTeX, and is the component most likely to invent a number. At every point where an agent would otherwise guess, vouch aims to make the correct action cheaper than the guess.

---

## Contents

0. [Five-minute tour](#0-five-minute-tour)
1. [Motivation, goals, non-goals](#1-motivation-goals-non-goals)
2. [Concepts](#2-concepts)
3. [Project layout and configuration](#3-project-layout-and-configuration)
4. [Recording API (Python)](#4-recording-api-python)
5. [Derived values, claims and tables: `vouch_values.py`](#5-derived-values-claims-and-tables-vouch_valuespy)
6. [Formatting](#6-formatting)
7. [LaTeX interface](#7-latex-interface)
8. [Freshness](#8-freshness)
9. [Change notification](#9-change-notification)
10. [Provenance CSV](#10-provenance-csv)
11. [Checks](#11-checks)
12. [CLI reference](#12-cli-reference)
13. [LLM interface](#13-llm-interface)
14. [Non-Python experiments: `vouch run`](#14-non-python-experiments-vouch-run)
15. [Workflow integration](#15-workflow-integration)
16. [Integrity, performance, compatibility](#16-integrity-performance-compatibility)
17. [Prior art](#17-prior-art)
18. [Lessons carried over from asqc](#18-lessons-carried-over-from-asqc)
19. [Implementation plan](#19-implementation-plan)
20. [Open questions and risks](#20-open-questions-and-risks)

---

## 0. Five-minute tour

```console
$ pip install vouch
$ vouch init                      # finds paper/main.tex, writes vouch.toml, copies vouch.sty
```

**Record** in the experiment by decorating the function that computes the numbers. Every call's result is recorded, keyed by the function and its arguments, together with the call itself, so each number knows exactly which code and which arguments produced it (§4.3a):

```python
# experiments/train.py
import vouch

@vouch.track(over="seed")                      # seeds are combined into mean ± std
def evaluate(dataset: str, model: str, seed: int = 0) -> dict:
    ...
    return {"acc": acc, "loss": loss}

for seed in range(5):
    evaluate("cifar", "resnet", seed=seed)     # → evaluate.cifar.resnet.acc, evaluate.cifar.resnet.loss
```

Formats, directions and descriptions come from `[metrics]` patterns in `vouch.toml`, written once for the whole project:

```toml
[metrics]
"*.acc" = { fmt = ".1pct", better = "higher", desc = "top-1 test accuracy" }
```

```console
$ python experiments/train.py                  # run it normally
```

Two other ways to record, for other situations (§4):
- **No change to the code at all:** list the function in `vouch.toml` (`[[track]] function = "experiments/train.py::evaluate"`) and run `python -m vouch.exec experiments/train.py` (§4.3b).
- **A number that isn't a function's return value:** `vouch.record("cifar.resnet.acc", acc, desc=…)` for one value, or `vouch.record_all(metrics, prefix="cifar.resnet")` for a dict of them (§4.1–4.3).

**Cite** it in the paper:

```latex
\usepackage{vouch}
...
ResNet-50 reaches \vouch{evaluate.cifar.resnet.acc} top-1 accuracy.
```

**Build** the generated values, then compile as usual:

```console
$ vouch build
wrote paper/vouch-values.tex (1 value), .vouch/CATALOG.md
$ latexmk -pdf paper/main.tex      # "ResNet-50 reaches 93.2 ± 0.4% top-1 accuracy." (click: the value's provenance)
```

**Check** it, locally, in pre-commit, or in CI:

```console
$ vouch check
✓ 1 value fresh · 0 claims · 0 figures
```

**Change the code** and vouch notices:

```console
$ vouch check
✗ stale  experiments.train   src/models.py::ResNet.forward changed since the run (2026-09-12)
         cites: evaluate.cifar.resnet.acc (main.tex:41)
         fix:   python experiments/train.py   (or: vouch accept experiments.train --why "...")
FAILED (1 error)
```

**Re-run it**, and the value moved:

```console
$ python experiments/train.py && vouch build
wrote paper/vouch-values.tex (1 value, 1 CHANGED)

  CHANGED  evaluate.cifar.resnet.acc   93.2 ± 0.4% → 91.0 ± 0.5%   (mean Δ −2.2 pts, −2.4%)
    main.tex:41   "ResNet-50 reaches \vouch{evaluate.cifar.resnet.acc} top-1 accuracy, the best of all models."
  → re-read the sentences above, then: vouch ack evaluate.cifar.resnet.acc   (or: vouch review)
```

---

## 1. Motivation, goals, non-goals

### 1.1 Motivation

Numbers in papers go wrong in four ways:

1. **Typed wrong.** Transcription errors, stale copies, or rounding done by hand.
2. **Never produced.** Written from memory or a draft, or hallucinated by an LLM.
3. **Out of date.** The code changed or the experiment was re-run, and the paper didn't follow.
4. **Prose invalidated.** The number was updated correctly, but the sentence around it ("the best of all models", "roughly doubles") is now false.

The asqc/ project handled 1–3 for one manuscript with a hand-maintained registry (`\vresult{key}{value}`, a ~1,080-line registry of entries, ~1,700 lines of per-number extractor functions), a git-ancestry freshness gate, and a pre-commit hook. It worked, and it showed what a general tool should do differently:

- Record values where they are computed, instead of writing an extractor per number.
- Insert values, instead of typing them and then checking them.
- Hash code, instead of dating commits.
- Track changes to the functions a run actually executed, instead of whole files.
- Treat problem 4 as a first-class concern, not an afterthought.

### 1.2 Goals

| # | Goal |
|---|---|
| G1 | No hand-typed empirical numbers. Every number in the paper is inserted from a recorded value. |
| G2 | Every value traces to code (file:line), run, command, parameters, inputs and commit, from the terminal, the PDF (hover) or a CSV. |
| G3 | A result whose code changed since it was produced is detected, precisely enough that a formatting or unrelated edit never triggers a false alarm. |
| G4 | When a cited value changes, the sentences that cite it are surfaced and stay flagged until reviewed. |
| G5 | An LLM agent can find, cite, compare and verify values without guessing, and gets immediate feedback when it slips. |
| G6 | Adopting it is cheap: one `record_all()` call per experiment (or one `record()` per value), and experiments run exactly as before. |
| G7 | `vouch check` finishes in under a second, fast enough for pre-commit, and never re-runs experiments. |
| G8 | Output is plain LaTeX that works in local builds, Overleaf and arXiv. |
| G9 | The core has no third-party dependencies (Python ≥ 3.10; `tomli` on 3.10 only). |

### 1.3 Non-goals (v1)

- Orchestrating or scheduling experiments. vouch is not DVC, Snakemake or Make.
- Re-running experiments automatically. (A `vouch reproduce` command is planned for v2; see §20.)
- Storing large artifacts. Only their hashes are stored; the files live wherever you keep them.
- Jupyter notebooks as tracked code (explicit runs work in notebooks, but cell code is not hashed).
- A typed-and-checked mode (`\vouch{key}{93.2\%}` as asqc did). A migration aid may come later.
- Markdown, Typst or HTML output. The renderer is kept pluggable so these can be added.

---

## 2. Concepts

| Term | Meaning |
|---|---|
| **Project** | The directory containing `vouch.toml`. All paths are stored relative to it, with forward slashes. |
| **Run** | One successful execution of an experiment, identified by a **run id**. The latest successful run per id is the *run of record*, stored in `.vouch/runs/<id>.json`. |
| **Value** | A recorded quantity with a **key**, a raw value, and metadata (format, unit, description, direction, call site). |
| **Key** | The name a value is recorded under **and** cited by. Keys are literal: the key you record is the key you cite. |
| **Claim** | A recorded or derived boolean with prose attached in the paper (`\vouchclaim`). It fails the check if it becomes false. |
| **Derived value** | A value computed from other values or artifacts by a function in `vouch_values.py`, evaluated by `vouch build`. |
| **Expected value** | A placeholder declared with `vouch.expect()` for a number the paper needs but no run has produced yet. |
| **Artifact** | A file a run produced (data, figure, table source, model), identified by path and content hash. |
| **Input** | A file or directory a run consumed. It is hashed, and a change makes the run stale. |
| **Code unit** | A piece of first-party code a run depended on: a function, a class body, or module-level code. It is identified as `path::qualname` and hashed semantically. |
| **Fresh / stale** | Whether a run's recorded code units and inputs still match the working tree. |
| **Acknowledged baseline** | The last reviewed value of each cited key. A difference from it is a *pending change*. |
| **Citation** | A use of a key in the paper's LaTeX (`\vouch`, `\vouchclaim`, `\vouchtable`, `\includegraphics`). |

### 2.1 Key grammar

```
key      := segment ("." segment)*          max 128 chars, case-sensitive
segment  := [A-Za-z0-9_-]+
```

Recommended convention: `<dataset>.<model>.<metric>`, e.g. `cifar.resnet.acc` or `imagenet.vit_b16.top5`.

Some keys are generated rather than recorded:

| Generated key | Source |
|---|---|
| `<key>.mean`, `.std`, `.n`, `.ci95`, `.min`, `.max` | subfields of a `Stat` value |
| `<run>.param.<name>` | run parameters (nested dicts are flattened with dots) |
| `<table>.<row>.<col>` | table cells; row and column names are slugified to `[A-Za-z0-9_-]` |

Two sources producing the same key is a `key-conflict` error.

---

## 3. Project layout and configuration

### 3.1 Layout

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

Everything except `.vouch/cache/` is committed. Committing is what lets co-authors, Overleaf, CI and fresh clones check the paper without the artifacts or a GPU.

### 3.2 Project root discovery

The CLI walks up from the current directory to the nearest `vouch.toml`. The Python API walks up from the entry script's directory; `VOUCH_ROOT` overrides both.

If no `vouch.toml` exists, the API falls back to the git top level, warns once (`no vouch.toml found; recording into <root>/.vouch — run 'vouch init'`), and records anyway. Adopting vouch should never break an experiment run.

### 3.3 `vouch.toml` reference

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

[[track]]                                  # functions whose results are recorded, no decorator (see §4.3b)
function       = "experiments/train.py::evaluate"
over           = "seed"

[tables.main]                              # presentation overrides for a recorded or derived table (§5.4)
highlight = { acc = "max" }
midrules  = [2]

[metrics]                                  # project-wide defaults by key glob (see §4.3)
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
annotate       = false                     # managed "% vouch: ..." trailing comments (see §7.8)

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

[check]                                    # per-check severity overrides (see §11)
severity = { "figure-untracked" = "info" }

[hook]
strict         = false                     # pre-commit runs `vouch check` (errors block); true adds --strict
```

Every setting has a working default. `vouch init` writes only `[[paper]] main` plus commented-out examples.

---

## 4. Recording API (Python)

There are three ways to record, and they combine freely in one run:

| Way | Code change | Use it when |
|---|---|---|
| `@vouch.track` on the function that computes the numbers (§4.3a) | one decorator | **the default.** Each value records the call that produced it (function, every argument, call site), which is the strongest provenance vouch has and what makes `vouch recompute` possible |
| `[[track]]` in `vouch.toml` + `python -m vouch.exec` (§4.3b) | none | the code can't or shouldn't import vouch: someone else's script, a frozen baseline, a quick look before committing to vouch |
| `vouch.record` / `vouch.record_all` (§4.1–4.3) | one line per value or per dict | a number that isn't a function's return value: an aggregate computed inline, a value read from a results file, a param |

### 4.1 Zero-configuration use: an implicit run

```python
import vouch

vouch.params(vars(args))                         # optional: parameters become citable values
acc = vouch.record("cifar.resnet.acc", evaluate(model), fmt=".1pct",
                   desc="top-1 test accuracy on CIFAR-10", better="higher")
```

- The first vouch call outside a `with vouch.run(...)` block starts an **implicit run**. Its id is the entry script's project-relative path with `/` replaced by `.` and without the `.py` suffix, e.g. `experiments.train`.
- The implicit run is finalized at interpreter exit, unless an uncaught exception occurred (detected through a `sys.excepthook` wrapper). A script that ends with `sys.exit(n)`, n ≠ 0, is not detectable from `atexit`. Use an explicit run when the exit code matters.
- `record()` returns the value unchanged, so it can be written inline.

### 4.2 Explicit runs

```python
with vouch.run("cifar_resnet", params=args) as run:
    run.input("data/cifar10.npz")                       # file or directory; hashed
    accs = [train_and_eval(seed) for seed in range(5)]
    run.record("cifar.resnet.acc", vouch.Stat.of(accs), fmt=".1pct",
               desc="top-1 test accuracy on CIFAR-10, mean ± std over seeds", better="higher")
    run.claim("cifar.resnet.no_divergence", all(math.isfinite(a) for a in accs),
              desc="no seed diverged")
    run.artifact("results/cifar_resnet.csv")            # hashed at finalize, after it is written
    run.table("ablation", rows, row_key="variant", fmt={"acc": ".1pct"},
              highlight={"acc": "max"}, desc="ablation of augmentations")
    plt.savefig("paper/figs/curve.pdf")                 # tracked automatically as a figure artifact
```

**Semantics:**

- **Atomic.** Nothing is written until the block exits cleanly. An exception, `KeyboardInterrupt` or `SystemExit(n≠0)` discards the run, and the previous record of this id is left intact.
- A run id must match the key grammar. Re-running an id replaces its record. Two runs with the same id in one process: the second replaces the first, with a warning.
- Runs cannot nest. Several sequential runs in one process are allowed, e.g. a sweep loop with one run per configuration.
- **Parameters.** `params=` accepts a dict, an `argparse.Namespace`, a dataclass, or any object with `to_dict()`/`to_container()` (OmegaConf, Pydantic). Values are flattened with dots and stored. Each one is citable as `<run>.param.<name>`, so "we train for 100 epochs" is `\vouch{cifar_resnet.param.epochs}` and cannot drift from the code.
- **Command.** `sys.argv` and the interpreter are recorded, which is what gives `vouch status` its exact re-run command.

### 4.3 Recording everything at once: `record_all`

Most experiments already end with their results in one object: a metrics dict from `evaluate()`, a `Trainer.evaluate()` result, a DataFrame of a sweep, or a `results.json`. One call registers all of it:

```python
metrics = evaluate(model, test_set)            # {"acc": 0.932, "loss": 0.211, "f1": {"macro": 0.90, "micro": 0.93}}
vouch.record_all(metrics, prefix=f"{dataset}.{model_name}")
# → cifar.resnet.acc, cifar.resnet.loss, cifar.resnet.f1.macro, cifar.resnet.f1.micro
```

Metadata comes from **project-wide metric conventions** defined once in `vouch.toml`, so a single-call registration is still fully described:

```toml
[metrics]                          # glob on the full key → defaults for any value that doesn't set its own
"*.acc"    = { fmt = ".1pct", better = "higher", desc = "top-1 test accuracy, {1} on {0}" }
"*.loss"   = { fmt = ".3f",   better = "lower",  desc = "test cross-entropy, {1} on {0}" }
"*.f1.*"   = { fmt = ".3f",   better = "higher", desc = "{-1} F1, {1} on {0}" }
"*.time_s" = { fmt = ".0f",   unit = "s",        better = "lower", desc = "wall-clock training time, {1} on {0}" }
```

In a `desc` template, `{0}`, `{1}`, … are key segments and `{-1}` is the last one, so `cifar.resnet.acc` gets "top-1 test accuracy, resnet on cifar". This matters for readers and matters more for agents: every key arrives described and searchable.

**Signature:**
```python
vouch.record_all(values, *, prefix=None, fmt=None, desc=None, unit=None, better=None,
                 include=None, exclude=None, row_key=None, stats=False, table=None) -> values
```

| Argument | Meaning |
|---|---|
| `values` | a dict (nested dicts are flattened with dots), a dataclass, a pandas Series or DataFrame, or a **path** to a `.json`, `.jsonl` or `.csv` file (read, recorded, and registered as an input of the run) |
| `prefix` | prepended to every key |
| `fmt`, `desc`, `unit`, `better` | a single value for all keys, **or** a dict mapping key or glob → value (`fmt={"*.acc": ".1pct"}`) |
| `include`, `exclude` | globs selecting which keys to keep (e.g. `exclude=["*.runtime", "*.epoch"]` for framework noise) |
| `row_key` | for DataFrames or lists of dicts: the column naming each row, giving keys `<prefix>.<row>.<col>` |
| `stats` | `True` turns lists of numbers into `Stat.of(list)` (per-seed results); by default a 2-element list is a range and longer lists are tuples |
| `table` | also register the DataFrame or list of rows as a table under this key (§5.4), so one call gives both cell keys and a `\vouchtable` |

**Rules:**
- **Metadata precedence:** an exact-key argument, then the longest matching glob argument, then `[metrics]` in `vouch.toml`, then the type default.
- **Key sanitizing.** `/` becomes `.` (so `eval/accuracy` → `eval.accuracy`, the W&B/TensorBoard style). Other invalid characters become `_`. All renames are reported in **one** summary line, never one warning per key.
- **Scalars are unwrapped.** Single-element numpy arrays and torch tensors become scalars via `.item()`. Larger arrays and other non-scalar objects are skipped and listed in the summary.
- **Missing descriptions** after all defaults are applied produce one aggregated warning: `vouch: 12 keys recorded without desc (cifar.resnet.*); add a [metrics] pattern in vouch.toml`.
- `record_all` returns its input unchanged, so it can wrap an expression: `metrics = vouch.record_all(trainer.evaluate(), prefix=...)`.

Sweeps and multi-seed experiments reduce to one call:

```python
# a learning-rate sweep → sweep.<lr>.<metric> keys plus a citable table
vouch.record_all(sweep_df, prefix="sweep", row_key="lr", table="lr_sweep")

# per-seed lists → Stat values (mean ± std, n)
vouch.record_all({"acc": accs_per_seed, "loss": losses_per_seed}, prefix="cifar.resnet", stats=True)
```

The same single-step registration is available outside Python:
- `vouch run` ingests the program's whole `$VOUCH_VALUES` JSON (§14).
- `vouch import` registers an existing results file (§14.1).

### 4.3a Recording a function's results: `@vouch.track`

Decorating an experiment function records what it returns on every call, keyed by the call's arguments:

```python
@vouch.track(over="seed")
def evaluate(dataset: str, model: str, seed: int = 0, lr: float = 1e-3) -> dict:
    ...
    return {"acc": acc, "loss": loss}

for model in ("resnet", "vit"):
    for seed in range(5):
        evaluate("cifar", model, seed=seed)
# -> evaluate.cifar.resnet.lr_0_001.acc, .loss, evaluate.cifar.vit.lr_0_001.acc, ...
#    each a Stat over seeds 0..4
```

**Keys** are the function's name followed by **every argument**, defaults included, in signature order:
- A string argument is written as its value (`cifar`).
- A number, bool or `None` is written as `name_value` (`lr_0_001`, `seed_3`, `flag_true`), because a bare number says nothing about what it is.
- An enum is written as its member name; a short list of simple values as `layers_64-64`.
- Arguments that can't be written into a key (arrays, models, dicts, `self`) are left out of it and described in the call metadata instead (`<ndarray shape (3, 4)>`, `<list of 10>`).
- Methods use the class-qualified name (`Trainer.fit…`).
- Keys longer than 100 characters are shortened with a hash suffix, so they stay unique.
- `key="{dataset}.{model}"` replaces the scheme with a template, checked against the signature when the function is decorated.

**Results:**
- a number or `Stat` → the base key
- a tuple → **one key per element**, because `return mean, std` is several results: `.0`, `.1`, … by position, or named with `returns=("mean", "std")` (`.mean`, `.std`). A namedtuple uses its field names. Each element is its own value, so each can be cited on its own, and `over=` combines each element separately.
- a dict or dataclass → one key per (nested) field
- a DataFrame or list of rows → one key per cell
- a list of numbers → one value (a series); its elements are citable as `key.0`, `key.1`, … (§4.5)
- `None` → nothing (warned once)

```python
@vouch.track(returns=("mean", "std"), desc="bootstrap accuracy ({-1})")
def bootstrap(model: str, n: int = 1000):
    ...
    return mean, std
# -> bootstrap.vit.n_1000.mean  "bootstrap accuracy (mean)"
#    bootstrap.vit.n_1000.std   "bootstrap accuracy (std)"
```

A `returns=` whose length doesn't match what the function returned warns once and falls back to positions. If the pair really is a mean and a standard deviation and you want it typeset as one `mean ± std`, return `vouch.Stat(mean, std, n)` instead.

`fmt`, `desc`, `unit`, `better`, `include` and `exclude` work as in `record_all`, and so do `[metrics]` defaults. A `desc` may use the same `{0}`, `{-1}`, `{key}` templates as `[metrics]`, filled per key.

**`over=`** names arguments whose calls are combined when the run ends. Numbers become a `Stat` (mean, std, n); a value that is the same on every call is kept as is; anything else is skipped with a note. Calling twice with the same `over` value warns, and both calls count.

**Every value records its call:**

```json
"call": {"function": "exp.py::evaluate",
         "args": {"dataset": "cifar", "model": "resnet", "lr": 0.001},
         "over": {"seed": [0, 1, 2, 3, 4]}, "calls": 5,
         "results": [0.931, 0.935, 0.929, 0.934, 0.932],
         "seconds": [612.4, 598.1, 605.9, 610.2, 601.7], "sites": ["exp.py:20"]}
```

- The value's `site` is the function's definition. `call.sites` are where it was called.
- `call.seconds` is how long each call took (wall clock, 4 significant digits), in the same order as `results`. It is always recorded, at the cost of two clock reads per call.
- `call.results` holds each call's own result, in the order of the `over` lists (for combined values, up to 100 calls). The mean ± std in the paper can always be traced back to the per-seed numbers behind it.
- `call.via` is `"[[track]]"` when the function was listed in `vouch.toml` rather than decorated (§4.3b), because the code itself then shows no decorator.
- `vouch trace`, tooltips, the provenance appendix (§7.4) and `vouch explore` show all of it: `recorded by evaluate(dataset=cifar, model=resnet, lr=0.001) over seed=0..4 (5 calls)`, `took 10.1 ± 0.1 min per call, 50.5 min in all`, each call's result (with its duration in `trace` and `explore`: `seed=0: 0.931 (10.2 min)`), and the function with its definition and call sites.

**Timing is a value too, by default.** Every tracked call also records its duration as a value of its own, `<key>.time`, in seconds (unit `s`): a float for a single call, a `Stat` over the `over=` calls (so `\vouch{evaluate.cifar.resnet.lr_0_001.time.mean}` is the mean seconds per seed), with each call's duration in its `call.results`. Measuring costs two clock reads; finding out later how long an experiment took means running it again, so vouch always keeps it.
- `time="walltime"` names it differently; `time=False` leaves the value out (the durations stay in the call's provenance).
- If the function's result already has a field named `time`, the result wins. That is silent by default, and a warning when `time=` asked for the name.
- Timing values are marked (`"timing": true` in the record). `vouch explore` tags them, and `vouch check` doesn't count them among values nobody cites.
- Durations differ on every run. A cited one is reported as changed after a re-run unless its printed precision hides the difference (then it is `hidden`, §9.2), so cite it with a coarse format.

The clock is `time.perf_counter()` around the call. For an async function that includes the time spent awaiting. For work a GPU does asynchronously, it includes whatever the function waits for before returning, which is the usual case when a function returns a number.

Freshness comes from §8.2: editing `evaluate` makes these values stale; editing an unrelated function doesn't.

**Bookkeeping never breaks the experiment:**
- The result passes through unchanged.
- An exception inside the function records nothing and propagates.
- A recording problem is a warning, and an error only with `VOUCH_STRICT=1`.
- Async functions are supported.

Because a tracked value records the exact code, the function, and every argument (seeds included), it can in principle be **recomputed**. See `vouch recompute` in §20.

### 4.3b Tracking without touching the code: `[[track]]` and `vouch.exec`

The functions to track can be listed in `vouch.toml` instead of decorated. The effect is the same as `@vouch.track` with the same options (same keys, same `call` metadata, same `over=` combining):

```toml
[[track]]
function = "experiments/train.py::evaluate"      # path::qualname; fnmatch patterns on both
over     = "seed"
desc     = "top-1 test accuracy"

[[track]]
function = ["models.py::Trainer.fit", "*::score_*"]   # a list is fine; no "::" means any file
key      = "{dataset}.{model}"                   # the same fields as the decorator: over key
returns  = ["mean", "std"]                       # returns time name fmt desc unit better include exclude
time     = false                                 # no <key>.time value (on by default)
```

```console
$ python -m vouch.exec experiments/train.py --epochs 90     # the script doesn't import vouch
vouch: recorded run experiments.train: 12 value(s) → .vouch/runs/experiments.train.json
```

**How it works.** The function tracking of §8.2 already sees every first-party function start. For a function a rule names, it keeps listening to that one code object (a `PY_RETURN` local event), reads the arguments from the frame when the call starts, and records the return value when it ends. Functions no rule names cost exactly what they cost before: one callback, then disabled.

**Running it.** Rules apply to any process that imports vouch: a script that already uses `vouch.record` picks up `[[track]]` with no further change. A script that doesn't import vouch runs under `python -m vouch.exec script.py args…`. That starts tracking before the script's first line, so the whole script is tracked by function (nothing ran "before `import vouch`"). The recorded command is the `vouch.exec` one, so the `fix:` line vouch prints for a stale run re-runs it the same way.

**Mistakes are reported, never raised:**
- A rule that names no function the script ran: `vouch: vouch.toml [[track]] 'train.py::evaluat' matched no function that ran`. This is reported once the script is over, so a function that runs only in a later `with vouch.run()` block isn't a false alarm.
- A rule whose `over=` or `key=` names an argument the function doesn't take, or an unknown field: a warning naming the rule.
- A generator or async generator (its return value isn't its result): skipped with a warning to decorate it or return a value.
- A function that is also decorated with `@vouch.track`: the decorator wins; it is recorded once.
- Tracking unavailable (Python < 3.12, `VOUCH_TRACE=0`): one warning saying `[[track]]` needs it and suggesting the decorator.

**Limits.** Only first-party functions (§8.2) can be listed, not library code. Module bodies, lambdas and comprehensions never match. A call that raises records nothing.

### 4.4 API reference

| Call | Purpose |
|---|---|
| `vouch.run(id, params=None, prefix=None)` | Context manager for an explicit run. `prefix` is prepended to every key recorded in the run. |
| `vouch.record(key, value, *, fmt=None, unit=None, desc=None, better=None)` → value | Record a value. `better ∈ {"higher", "lower", None}`. |
| `vouch.record_all(values, *, prefix=None, fmt=None, desc=None, …)` → values | Record every value in a dict, DataFrame or results file in one call (§4.3). |
| `vouch.claim(key, holds, *, desc=None, values=None)` → holds | Record a boolean claim. `values` is an optional dict of the numbers it is about, used in messages and tooltips. |
| `vouch.input(path, *, mode=None)` | Declare an input file or directory. `mode` overrides `freshness.input_hashing`. |
| `vouch.artifact(path, *, kind=None)` | Declare an output file. `kind ∈ {"figure", "data", "model", "file"}`, inferred from the extension. |
| `vouch.table(key, data, *, columns=None, row_key=None, fmt=None, highlight=None, desc=None)` | Record a table: a list of dicts, a list of lists plus `columns`, or a pandas DataFrame. |
| `vouch.params(obj)` | Set the implicit run's parameters. |
| `vouch.expect(key, *, desc, producer=None, fmt=None, unit=None, better=None)` | Declare a placeholder (see §5.5). |
| `vouch.Stat.of(samples)` / `vouch.Stat(mean, std, n, *, min=None, max=None)` | A summary statistic. `std` is the sample std (ddof = 1). `ci95` uses Student-t quantiles. |

Module-level functions act on the active run: the explicit one if inside a `with`, otherwise the implicit one. `run.<method>` is the same function bound to a specific run.

### 4.5 Value types

| Python value | Stored `type` | Default rendering |
|---|---|---|
| `bool` | `bool` | `true`/`false` (usually cited through claims) |
| `int` (incl. numpy ints) | `int` | `d` |
| `float` (incl. numpy floats) | `float` | `format.default_float` (`.3g`) |
| `str` | `str` | LaTeX-escaped text (e.g. a selected model name) |
| `tuple`/`list` of numbers | `tuple` | `a--b` for 2 elements (a range), comma-joined otherwise |
| `Stat` | `stat` | `mean \pm std` |

NaN and ±inf are stored as `{"$float": "nan"}` etc. Recording one prints a warning: a non-finite number in a paper is almost always a bug.

**Citing part of a value.** A `Stat` exposes `key.mean`, `key.std`, `key.n`, `key.min`, `key.max` and `key.ci95`. A `tuple` value (up to 64 elements) exposes each element by position: `vouch.record("ci", (0.91, 0.95))` makes `\vouch{ci.0}` and `\vouch{ci.1}` citable, each with the value's fmt and unit, and each overridable with `\vouch[fmt]{ci.1}`. A key recorded explicitly under the same name (`ci.0`) takes precedence over the element.

### 4.6 Validation, with warnings phrased as fixes

| Condition | Message |
|---|---|
| no `desc` | `vouch: cifar.resnet.acc has no desc; add desc="..." so readers and agents can find it` |
| malformed key | `vouch: "CIFAR acc" is not a valid key; use dots/underscores, e.g. "cifar.acc"` |
| non-finite | `vouch: cifar.resnet.loss is NaN; the run will record it, but check will flag it` |
| `better` on a non-number | `vouch: better= only applies to numeric values` |
| key recorded twice in one run | the last write wins, with a warning naming both call sites |

Warnings go to stderr. They never raise, because an experiment must never die over bookkeeping. The one exception is a malformed key when `VOUCH_STRICT=1`.

### 4.7 Code tracking

Tracking starts at `import vouch` (see §8.2 for the mechanism). For complete coverage, import vouch before other first-party modules, or run the script as `python -m vouch.exec script.py …` (§4.3b), which starts tracking before the script's first line. Modules that were already imported when tracking started are tracked at module granularity. That is correct, just coarser.

**Figure tracking.** When matplotlib is imported, before or after vouch, `Figure.savefig` is wrapped (and with it `plt.savefig`). Every figure saved to a path while vouch is recording becomes a figure artifact of the active run, with the file:line of the `savefig` call. A path without an extension gets the one matplotlib adds (`format=` or `rcParams["savefig.format"]`). Saving to a file object is not tracked.

vouch never imports matplotlib. If it is already loaded, it is patched at `import vouch`. Otherwise a finder on `sys.meta_path` waits for `matplotlib.figure` to be imported, patches it, and removes itself. The CLI (and so `vouch build` evaluating `vouch_values.py`) records no figures.

### 4.8 Run record format (`.vouch/runs/<id>.json`)

The file is deterministic: keys are sorted, and every map is written one entry per line, so a changed value is a one-line diff (asqc's `snapshot.write` style).

```json
{
  "schema": "vouch/1",
  "run": "cifar_resnet",
  "status": "complete",
  "entry": "experiments/train.py",
  "command": ["python", "experiments/train.py", "--model", "resnet50", "--seeds", "5"],
  "params": {
    "model": "resnet50",
    "optim.lr": 0.1,
    "seeds": 5
  },
  "started": "2026-09-12T14:03:11Z",
  "duration_s": 3412.5,
  "git": {"commit": "0fdc530e7c1b9f6a2d8e4b3c5a7f9e1d2c4b6a8f", "dirty": false},
  "env": {"python": "3.13.5", "platform": "win-amd64", "packages": {"numpy": "2.1.0", "torch": "2.5.1"}},
  "code": {
    "granularity": "function",
    "units": {
      "experiments/train.py::<module>": "9f2c1a0b44e1d7a3",
      "experiments/train.py::main": "41d0e7c2aa9b1f05",
      "src/models.py::<module>": "77aa01c3d5e9b210",
      "src/models.py::ResNet": "0c1d2e3f4a5b6c7d",
      "src/models.py::ResNet.forward": "c03b99e1f2a4d6b8"
    },
    "files": {
      "experiments/train.py": "e1b2c3d4e5f6a7b8",
      "src/models.py": "7a8b9c0d1e2f3a4b"
    }
  },
  "inputs": {
    "data/cifar10.npz": "sha256:5f1c…"
  },
  "values": {
    "cifar.resnet.acc": {"type": "stat", "value": {"mean": 0.93214, "std": 0.0041, "n": 5}, "fmt": ".1pct", "desc": "top-1 test accuracy on CIFAR-10, mean ± std over seeds", "better": "higher", "site": "experiments/train.py:88"}
  },
  "claims": {
    "cifar.resnet.no_divergence": {"holds": true, "desc": "no seed diverged", "site": "experiments/train.py:91"}
  },
  "artifacts": {
    "paper/figs/curve.pdf": {"hash": "sha256:a41e…", "kind": "figure", "site": "experiments/train.py:102"},
    "results/cifar_resnet.csv": {"hash": "sha256:09bd…", "kind": "data", "site": "experiments/train.py:95"}
  },
  "tables": {
    "ablation": {"columns": ["variant", "acc"], "row_key": "variant", "rows": [["none", 0.912], ["mixup", 0.931]], "fmt": {"acc": ".1pct"}, "highlight": {"acc": "max"}, "desc": "ablation of augmentations", "site": "experiments/train.py:97"}
  },
  "record_hash": "sha256:3c9e…"
}
```

- `code.units` holds semantic hashes, truncated to 16 hex characters. `code.files` holds raw file hashes, used only to classify an edit as cosmetic.
- `record_hash` is SHA-256 over the canonical JSON of the record without that field (sorted keys, compact separators). `vouch check` recomputes it and reports `store-edited` on a mismatch (§16.1).

---

## 5. Derived values, claims and tables: `vouch_values.py`

Many numbers in a paper are computed *from* results: a difference, a ratio, a best-of, a mean over datasets, or a table assembled from several runs. These live in `vouch_values.py` (configurable). `vouch build` evaluates them. They are never computed in anyone's head, human or LLM.

```python
# vouch_values.py
import vouch
from vouch import gt

@vouch.derive("cifar.resnet_vs_vit.pts", fmt=".1f", unit="points",
              desc="ResNet minus ViT top-1 accuracy, percentage points", better="higher")
def _(v):
    return 100 * (v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"])

@vouch.claim("cifar.resnet_beats_vit", desc="ResNet top-1 > ViT top-1 on CIFAR-10")
def _(v):
    return gt(v["cifar.resnet.acc.mean"], v["cifar.vit.acc.mean"])

@vouch.derive("sweep.best_lr", inputs=["results/lr_sweep.csv"], fmt=".0e",
              desc="learning rate with the best validation accuracy")
def _(v, sweep):                      # sweep: list[dict] (or a DataFrame with as_frame=True)
    return float(max(sweep, key=lambda r: float(r["val_acc"]))["lr"])

@vouch.table("main", columns=["Model", "CIFAR-10", "ImageNet"], row_key="Model",
             fmt={"CIFAR-10": ".1pct", "ImageNet": ".1pct"},
             highlight={"CIFAR-10": "max", "ImageNet": "max"}, second="underline")
def _(v):
    return [[m, v[f"cifar.{m}.acc"], v[f"imagenet.{m}.acc"]] for m in ("resnet", "vit", "convnext")]

vouch.alias("resnet", "evaluate.cifar.resnet.lr_0_001")   # \vouch{resnet.acc} for a long tracked key

vouch.expect("imagenet.convnext.acc", desc="ConvNeXt-T top-1 on ImageNet",
             producer="python experiments/train.py --dataset imagenet --model convnext")
```

`vouch.claim` and `vouch.table` are the same functions experiments use to record claims and tables. Called with a value (`vouch.claim(key, holds)`), they record it in the active run. Called with only a key and options, they are decorators for `vouch_values.py`.

The values modules are inert outside `vouch build`: importing one from a notebook or a test registers nothing and runs no definition. Inside a build they must not record: `vouch.record()` (or a `savefig`) there is an error, because a value computed from other values belongs in a `@vouch.derive`.

### 5.1 The accessor `v`

- `v[key]` returns the raw value: `Stat` for a stat, `float` for a float, `bool` for a claim, and a list of row dicts for a table. Subfield keys such as `.mean` and aliases (§5.7) work.
- `v.keys("cifar.*.acc")` lists the recorded keys (from runs) matching a glob, for best-of and mean-over-datasets definitions. The list itself is a dependency: a new matching key makes the derivation out of date.
- Every access is logged, with a content hash of the value read. The logged keys become the derivation's **dependencies**, and through them the runs it rests on. They feed freshness (§8) and appear in tooltips, the provenance appendix, the CSV, `vouch trace` ("from …", and "feeds …" on the keys read) and failure messages.
- Accessing a pending (expected) key raises `vouch.Pending`. The derivation becomes pending in turn, and so does everything citing it. (Placeholders arrive with `expect`, §5.5.)
- Accessing an unknown key fails the derivation with a did-you-mean suggestion: `derive-error: cifar.gap reads 'cifar.resnet.ac', which is not recorded by any run (did you mean cifar.resnet.acc?)`.
- Derivations may use other derived keys. Evaluation is lazy and memoized, with cycle detection (`derive-cycle`, reported on every member of the cycle). A derivation that reads a failed one fails too, naming it.
- A definition that raises is reported as `derive-error` with the exception and the file:line where it happened; the other definitions are still evaluated.

### 5.2 Artifact inputs

`inputs=[...]` lists paths the function receives as positional arguments after `v`. Loaders are chosen by extension:

| Extension | Loaded as |
|---|---|
| `.csv` | `list[dict[str, str]]` (or a DataFrame with `as_frame=True`) |
| `.json` | the parsed JSON |
| `.jsonl` | a list of parsed records |
| anything else | `pathlib.Path` |

Each input must be an **artifact of some run**, or be listed under `[inputs] external`. A derivation that reads a file nobody produced is a provenance hole (`untracked-input` error).

### 5.3 Claim helpers

A claim function returns a `bool`, or a `Verdict` produced by these helpers:

| Helper | Holds when |
|---|---|
| `gt(a, b)`, `ge`, `lt`, `le` | the comparison holds |
| `between(x, lo, hi)` | `lo ≤ x ≤ hi` |
| `approx(x, y, rel=0.05)` | `|x − y| ≤ rel·|y|` |
| `all_of(...)`, `any_of(...)` | combinations of the above |

A `Verdict` carries `holds`, a human-readable `explanation` (`0.932 > 0.912`) and a relative **margin**: how far the claim is from flipping, relative to the boundary it is measured against. `gt(a, b)` holds by `(a − b)/|b|`; `between` by the distance to the nearer bound; `approx` by the tolerance left; `all_of` is as fragile as its most fragile part. A failing verdict has a negative margin. A `Stat` is compared by its mean, and a `Verdict` is truthy, so `if vouch.gt(a, b):` works.

A claim that holds by less than `changes.claim_margin` (default 1%) is reported as `fragile`: a warning naming the claim, its explanation and margin. When a value changes, you are warned that a claim is close to flipping before it flips.

The helpers work in experiments too: `vouch.claim("cifar.resnet_beats_vit", vouch.gt(acc_r, acc_v), desc=…)` records the explanation and margin with the claim. Tooltips, the appendix and `vouch trace` show `0.932 > 0.912 (margin 2.2%)`.

### 5.4 Tables

A table is recorded data, `run.table(...)`, or derived data, `@vouch.table(...)`. It is rendered at **build** time, so its presentation can be changed without re-running anything, either in the table definition or with overrides under `[tables.<key>]` in `vouch.toml`.

Rendering produces only the **body rows** (`a & b & c \\`). You write the `tabular`/`booktabs` environment and the header, so styling stays yours.

- `highlight={"col": "max" | "min"}` bolds the best cell per column. `second="underline"` marks the runner-up. "Best" respects each column's `better` direction when highlight is `"best"`.
- `Stat` cells render `mean \pm std`.
- `midrules=[2, 5]` inserts `\midrule` after the given body rows.
- Each cell is citable as `\vouch{<table>.<row>.<col>}` and has a tooltip.
- `[tables.<key>]` in `vouch.toml` overrides `fmt` (per column, merged), `highlight`, `second` and `midrules` for a recorded or derived table, so presentation changes need neither a re-run nor a code edit.
- A derived table's cells carry the definition's provenance: the function, the keys it read and their runs.

### 5.5 Expected values (placeholders)

`vouch.expect(key, desc=…, producer=…)` declares a number the paper needs before any run produces it. Placeholders are how a draft, whether an LLM's or a person's, can be written ahead of the experiments without inventing numbers:

- In the PDF it renders as a boxed **[pending: key]**, which is visibly different from an unknown key's **??key**.
- `vouch todo` lists pending keys with their `producer` commands.
- The check reports it as `pending`: a warning in drafts, an error under `--strict` and in `final` builds.
- Once a run records the key, the placeholder resolves automatically. A leftover `expect()` for a key that now exists is an `info` message ("expectation met; you can delete it").

### 5.6 Evaluation and persistence

`vouch build` imports each values module and evaluates every definition. It writes `.vouch/derived.json` (committed, deterministic, one definition per line, sealed with a `record_hash` like a run record), which holds:

- `code`: the semantic hash (§8.1) of each values module and of every first-party module it imported, such as a `helpers.py`
- per definition: its kind, function and site; its results (values with their fmt, unit, desc and better; a claim's holds, explanation and margin; a table's rows); `deps`, every key it read with the content hash of the value read; `runs`, the runs those values came from; `inputs`, the files it read with their hashes
- `problems`: the errors and warnings evaluation found (`derive-error`, `derive-cycle`, `untracked-input`, …), so every later build and check reports them until they are fixed

```json
"cifar.gap": {"kind": "value", "function": "vouch_values.py::gap", "site": "vouch_values.py:7",
              "deps": {"cifar.resnet.acc.mean": "6e4001c6680470dc", "cifar.vit.acc.mean": "74c29573dc80a43d"},
              "runs": ["cifar_resnet", "cifar_vit"],
              "values": {"cifar.gap": {"type": "float", "value": 2.02, "fmt": ".1f", "unit": "points", …}}}
```

**Code is hashed per module, not per definition.** Derivations are cheap, and a module-level change (a constant, a helper) can affect any of them, so the whole set is re-evaluated when any of its code changes semantically. Comments, docstrings and formatting don't count (§8.1).

**When anything moved, rebuild; otherwise don't even import.** A build first compares `derived.json` with the present: the code hashes, every dependency's current value hash, every input's file hash, and the list of values modules. If nothing moved, it uses the stored results without importing anything, so repeated builds are instant. If something moved, it re-evaluates everything and rewrites the file (only if its content changed).

`vouch check` does **not** evaluate definitions: checking never imports pandas or runs user code. It loads `derived.json`, makes the same comparison, and reports one `out-of-sync` error listing what moved (`vouch_values.py changed`, `cifar.vit.acc.mean changed (read by cifar.gap)`, `input data/x.csv changed (read by …)`), with the fix `vouch build`. A key cited in the paper and defined in a values module that hasn't been built yet is covered by that error, not reported as `unknown-key`: check finds such keys by reading the literal first arguments of `derive`/`claim`/`table`/`alias` calls in the source. A hand edit of `derived.json` is `store-edited`.

**Freshness.** A derived value rests on the runs it read. If one of them is stale, the check reports that run as stale and names the derived key among the citations, exactly as for a directly recorded value. Tooltips and the appendix show each run's state.

**Imports.** Each build imports the values modules afresh (and their first-party helpers), with the module's directory and the project root on `sys.path`, and removes them from `sys.modules` afterwards.

### 5.7 Aliases

`vouch.alias(short, full)` in a values module lets the paper cite `full`, and every key under it, by a shorter name:

```python
vouch.alias("resnet", "evaluate.cifar.resnet.lr_0_001")     # a long @vouch.track key
```

```latex
ResNet reaches \vouch{resnet.acc} (\vouch[.3f]{resnet.acc.mean}).
```

- An alias covers the key itself and every key under it: subfields (`.mean`), dict fields, table cells, and a whole table (`\vouchtable{short}`).
- An aliased key has the provenance of the original, plus "alias of `evaluate.cifar.resnet.lr_0_001.acc`" in its tooltip, appendix entry and `vouch trace`.
- Aliases can be read in definitions (`v["resnet.acc.mean"]`) and may name other aliases.
- An alias whose target matches nothing is an `alias-target` warning. An alias that collides with a recorded key is a `key-conflict`.
- Aliases are stored in `derived.json` like definitions, so check resolves them without running anything.

---

## 6. Formatting

Formatting happens at **build** time from the raw recorded value, so changing a format never requires a re-run. A value has a default `fmt` (set at record time); a citation can override it with `\vouch[fmt]{key}`.

### 6.1 Grammar

Format strings must be safe inside LaTeX source and inside `\csname`:

- `%` starts a comment in LaTeX.
- Non-ASCII characters are unsafe in control-sequence names.
- `babel` makes `:;!?` active in some languages.

The grammar is therefore ASCII-only and uses letters for those symbols:

```
fmt     := [sign] [","] ["." precision] [type] {suffix}
sign    := "+"                               always show the sign
","                                          thousands grouping, rendered {,}
type    := "f" | "e" | "g" | "d" | "pct" | "s"
suffix  := "x"      append \times           (multipliers: 1.62\times)
         | "u"      append the unit          (12.3\,ms)
         | "ci"     Stat as mean [lo, hi]    (95% CI)
         | "to"     tuple as a \to b         (56 \to 32)
```

On the Python side, `fmt=".1%"` is accepted as an alias for `.1pct`, and so is `"{:.1%}"`. Aliases are normalized when recorded; LaTeX sources must use the canonical form.

Named formats from `[format] named` can be used anywhere a format can: `\vouch[pct1]{key}`.

### 6.2 Rendering table

| Raw value | fmt | Rendered LaTeX |
|---|---|---|
| `0.93214` | `.1pct` | `93.2\%` |
| `0.93214` | `.2f` | `0.93` |
| `18535` | `,d` | `18{,}535` |
| `1.87e-14` | `.1e` | `\ensuremath{1.9\times10^{-14}}` |
| `1.6247` | `.2fx` | `\ensuremath{1.62\times}` |
| `-0.9212` | `.2f` | `\ensuremath{-0.92}` (true minus sign) |
| `0.0213` | `+.1pct` | `\ensuremath{+2.1}\%` |
| `Stat(0.93214, 0.0041, 5)` | `.1pct` | `\ensuremath{93.2 \pm 0.4}\%` |
| `Stat(0.93214, 0.0041, 5)` | `.1pctci` | `93.2\% [92.7, 93.7]` |
| `(41.2, 53.4)` | `.0f` | `41--53` |
| `(56, 32)` | `dto` | `\ensuremath{56 \to 32}` |
| `12.34` + `unit="ms"` | `.1fu` | `12.3\,ms` (siunitx mode: `\qty{12.3}{\ms}`) |
| `"ResNet-50"` | `s` | `ResNet-50` (LaTeX-escaped) |

### 6.3 Rules

- **Rounding** is half-up by default, computed with `Decimal(repr(x))`: `0.125 → 0.13`, the way readers expect, not the banker's rounding of `format()`. `half_even` is available.
- **Math.** Output containing `\times`, `\pm`, `\to`, `^`, or a leading minus sign is wrapped in `\ensuremath{}`, so `\vouch` works in text and in math.
- **Default float format.** If no format is given, `.3g` is used, and the build prints an `info` note that a float was cited without an explicit format. This is where an unintended precision would otherwise hide.
- **siunitx mode** emits `\num{…}` / `\qty{…}{…}` instead of hand-built numbers, for papers that already use siunitx.
- **Pre-rendering.** Every (key, fmt) pair the paper actually uses is found by scanning (§7.7) and rendered in advance. LaTeX never computes anything.

---

## 7. LaTeX interface

### 7.1 Setup

```latex
\usepackage{vouch}               % or \usepackage[short,final]{vouch}
```

`vouch.sty` is plain LaTeX2e and needs nothing beyond a current kernel. At the end of the preamble it loads `hyperref` (unless the document already has), `xcolor`, and, for the tooltip and note modes, `pdfcomment`. Under `final` it loads none of them. It `\input`s the values file automatically (`\InputIfFileExists`), so no second line is needed.

### 7.2 Macros

| Macro | Renders | Notes |
|---|---|---|
| `\vouch{key}` / `\vouch[fmt]{key}` | the formatted value | Robust; works in text, math, captions, section titles and table cells. |
| `\vouchraw{key}` | the raw number, e.g. `0.93214` | Expandable, for pgfplots, `\pgfmathparse` and siunitx input. No tooltip. |
| `\vouchclaim{key}{prose}` | the prose | The tooltip shows the verdict. If the claim is false or unknown, a red marker follows unless `final`. |
| `\vouchtable{key}` | `\input` of `vouch-tables/<key>.tex` | Body rows only. |
| `\val`, `\vclaim`, `\vtable` | aliases | Only with package option `short`, and only if not already defined. |

Missing keys render the way `\ref` handles missing labels:

- **Unknown key:** `\textbf{??\detokenize{key}}`, plus `\PackageWarning{vouch}{Value `key' undefined}` and an end-of-document summary: *"There were undefined vouch values. Run `vouch build`."*
- **Pending key:** `\fbox{\scriptsize pending: key}` (or the placeholder text you configure).

### 7.3 Package options

| Option | Effect |
|---|---|
| `provenance=link\|tooltip\|note\|off` | how a reader gets from a number to where it came from (§7.4). Default `link`. |
| `tooltip=key\|value\|full` | how much tooltip or note text says (default `full`). `tooltip=off`, the v0.1 spelling, means `provenance=off`. |
| `highlight=off\|changed` | color values whose change is not yet acknowledged (default `changed`) |
| `final` | camera-ready: no links, tooltips, notes, colors or appendix, and none of `hyperref`/`pdfcomment`/`xcolor` is loaded on vouch's account, so no internal paths or commits leak into the submission. Pending or unknown keys and false claims still render their markers **and** raise a LaTeX error, so a final build cannot silently ship a placeholder or a falsified claim. |
| `short` | defines the aliases above |
| `values=FILE` | the generated values file (default `vouch-values.tex`) |

### 7.4 From a number to its provenance, in the PDF

Hover tooltips turned out not to be portable. Tested in 2026-09: Chrome's and Edge's built-in viewers and several Windows Store readers draw a focus box on a form-field tooltip but never show its text. So there are four modes:

| Mode | What the reader does | Works in |
|---|---|---|
| **`link`** (default) | clicks a number and lands on its entry in a generated **"Value provenance" appendix**; the entry links back to every page that cites it | every viewer (plain internal links, as used by `\ref`) |
| `tooltip` | hovers over a number | Acrobat, Firefox; not Chrome or Edge |
| `note` | hovers over or clicks a sticky-note icon beside each number | most desktop viewers; visually busy |
| `off` | — | — |

`examples/viewer-check/viewer-check.tex` compiles a one-page test of all three, so a team can check the viewers it uses.

**Link mode.** In drafts, every `\vouch` value, `\vouchclaim` prose span and generated table cell is a link, colored `vouchvalue` (a muted blue; redefine it with `\definecolor` to change it). At the end of the draft, or where `\vouchprovenance` is placed, the appendix lists every cited key in order of first citation. Each entry shows:

- the key and its rendered value, with "cited on p. 1, 3" (each page number links back)
- the description, the raw value, and the table it belongs to (for table cells)
- for a value recorded by a tracked function (§4.3a, §4.3b): the call (`recorded by evaluate(dataset=cifar, model=resnet, lr=0.001) over seed=0..2 (3 calls)`), how long the calls took (`took 60.5 +/- 9.94 ms per call, 181 ms in all`), each call's own result (`each call: seed=0: 0.936888; seed=1: 0.922687; seed=2: 0.939121`), and the function with its definition and call sites (`function train.py::evaluate at train.py:6, called at train.py:20`), plus "listed in `vouch.toml` [[track]]" when it wasn't decorated
- run, file:line (for a tracked value, the definition above) and command
- date, commit, and the run's current state (`fresh`, `STALE – models.py::f changed`, …)
- for an unacknowledged change, "CHANGED: was 93.2% (acked 2026-09-10)" in the highlight color

Some details follow from how PDF links work:

- **Page numbers** come from the `.aux` file, so they appear after the second LaTeX run, as with `\ref`. vouch writes its own aux entries instead of using `\label`, because amsmath takes `\label` over inside equation environments.
- **Links can't nest.** A claim's prose is one link, and values inside it don't make links of their own (they still appear in the appendix). Table-of-contents and list-of-figures lines are already links, so values in section titles and captions stay plain there.
- **hyperref.** If the document doesn't load it, vouch loads it with `hidelinks`, so the document's look is unchanged. If it does (e.g. with `colorlinks`), its settings win for claim prose; vouched numbers keep `vouchvalue`.

**Tooltip mode.** Values are wrapped in `pdfcomment`'s `\pdftooltip` (a widget annotation with `/TU`). With `tooltip=full`:

```
cifar.resnet.acc = 0.93214 (±0.0041, n=5)
run cifar_resnet · experiments/train.py:88
python experiments/train.py --model resnet50 --seeds 5
2026-09-12 14:03 UTC · git 0fdc530 · state: fresh
```

A tracked value's tooltip carries the same call lines as its appendix entry.

A tooltip is an unbreakable box, so claim prose gets its tooltip **word by word**: the claim still breaks across lines normally. A word that is itself a `\vouch{…}` keeps its own tooltip.

Tooltip and note text is sanitized for `\pdfstringdef`: backslashes, braces, `%`, `#`, `$`, `^`, `~` and `&` are replaced, and paths always use forward slashes. The `key` level shows only the key; `value` shows the key and raw value.

In every mode, a run's state (`fresh`, `STALE`, …) comes from `\vouch@state` lines. They depend on the working tree, so `vouch check` leaves them out when comparing the generated files (§11).

### 7.5 Changed-value highlighting

A cited value whose change is pending (§9) is rendered in a highlight color (`\colorbox` or text color, configurable). Skimming the PDF then shows exactly which sentences to re-read. Highlighting is off under `final`.

### 7.6 The values file

`paper/vouch-values.tex` is generated, sorted and deterministic. It has no timestamps, so an unchanged build is a byte-identical file.

```latex
% vouch-values.tex -- GENERATED by `vouch build`. Do not edit: `vouch check` detects edits.
% 38 values · 3 claims · 1 table · 1 pending
\vouch@set{cifar.resnet.acc}{}{\ensuremath{93.2 \pm 0.4}\%}{93.2 +/- 0.4\%}{<tooltip>}{0}
\vouch@set{cifar.resnet.acc}{.2f}{\ensuremath{0.93 \pm 0.00}}{0.93 +/- 0.00}{<tooltip>}{0}
\vouch@set{cifar.resnet_vs_vit.pts}{}{2.0}{2.0}{<tooltip>}{1}
\vouch@raw{cifar.resnet.acc}{0.93214}
\vouch@claim{cifar.resnet_beats_vit}{1}{<tooltip>}{0}
\vouch@pending{imagenet.convnext.acc}{python experiments/train.py --dataset imagenet --model convnext}
\vouch@table{main}{vouch-tables/main.tex}
```

The arguments are key, fmt (empty = default), rendered LaTeX, a plain-text form (used in PDF bookmarks, where `\vouch` must expand to text), tooltip, and changed-flag. Every recorded key is written at its default format, so co-authors on Overleaf can cite any recorded key without a local build; formats the paper overrides are written in addition. `\vouch[fmt]{key}` looks up `\csname vouch@v@key@fmt\endcsname`. The generated file's git diff is a readable record of every number that moved.

### 7.7 How the paper is scanned

The scanner (ported from asqc's `audit/numbers/check.py`) builds the file graph and finds every citation:

- **File graph.** Starting from each `[[paper]] main`, it follows `\input`, `\include`, `\subfile`, `\import`/`\subimport`, resolving `.tex` extensions relative to the main file's directory, as LaTeX does.
- **Comments** are blanked with offsets preserved (so line numbers stay true), respecting `\%`. Environments listed in `lint.skip_envs` are skipped.
- **Citations** found:
  - `\vouch[opt]{key}`, `\vouchraw{key}`, `\vouchclaim{key}{…}`, `\vouchtable{key}`
  - the aliases, if `\usepackage[short]{vouch}` is present
  - `\includegraphics[…]{path}`, resolved through `\graphicspath` and the extensions `.pdf .png .jpg .jpeg .eps .pgf .svg`
- **Macro definitions** (asqc's `rendered_keys` lesson). A citation inside a `\newcommand`, `\renewcommand`, `\def` or `\NewDocumentCommand` body counts only if that macro is used in the document, and its locations are the macro's use sites. A key hoisted into a macro that nothing uses does not count as cited.
- **Sentences.** For change notifications, each citation's surrounding sentence is extracted: from the previous sentence boundary (`. ! ?` followed by whitespace, or a blank line) to the next, lightly de-TeXed, and capped at 240 characters.

### 7.8 Source annotations (opt-in: `latex.annotate = true`)

`\vouch{key}` keeps the source clean, but it hides the number from someone reading the `.tex`. With annotations on, `vouch build` (or `vouch sync` on demand) maintains a **managed trailing comment** on every source line that cites values:

```latex
ResNet-50 reaches \vouch{cifar.resnet.acc} top-1 accuracy, \vouch{cifar.resnet_vs_vit.pts} points  % vouch: cifar.resnet.acc=93.2±0.4%, cifar.resnet_vs_vit.pts=2.0
```

- vouch owns only the ` % vouch: …` suffix. If the line already ends in a comment, the suffix goes after it (everything after the first `%` is a comment anyway).
- The suffix is plain text, not LaTeX, and is never read back as data.
- Disabling the option and running `vouch sync --strip` removes every annotation.

---

## 8. Freshness

A run is **fresh** when the code it executed and the inputs it read are unchanged since it recorded. Freshness is computed from content hashes. Git is not required; commits are recorded only as metadata.

### 8.1 Semantic hashing

A code unit's hash is SHA-256 (truncated to 16 hex characters) of `ast.dump(node, include_attributes=False)` after stripping docstrings. This is ported from asqc's `freshness._semantics`. As a result:

- **Comments, docstrings, blank lines, formatting and moving code around never change a hash.** Such edits are *cosmetic*. They are detected by comparing raw file hashes, reported as `info`, and never fail.
- Any change to logic changes the hash: code, defaults, decorators, annotations (which can matter to dataclasses) and constants. Renaming a local variable counts as a change. It is rare, and when it happens you record the judgement with `vouch accept` (§8.5).

**Units per file:**

| Unit | Contents |
|---|---|
| `path::<module>` | module-level statements other than definitions |
| `path::Class` | the class header (name, bases, keywords, decorators) and class-level statements other than methods and nested classes |
| `path::func`, `path::Class.method` | the whole definition: decorators, signature, defaults, body, including nested functions |

Definitions are left out of the enclosing unit, because each definition's decorators, signature and defaults are already in its own unit. So reordering functions never changes a hash, and neither does editing one a run never executed.

Nested definitions and lambdas (`f.<locals>.g`, `<lambda>`, `<genexpr>`) belong to their enclosing top-level unit. If a qualname is defined more than once (conditional definitions, a property setter), all of its definitions are hashed together in source order.

### 8.2 Function-level tracking (Python ≥ 3.12)

On `import vouch`, vouch registers a `sys.monitoring` (PEP 669) `PY_START` callback under a free tool id. It tries 3, 4, 5 and 2 in that order, so debuggers (0) and coverage.py (1) are never displaced. The callback returns `DISABLE`, so it fires at most once per code object for the whole process. Its cost is one call per distinct function, however hot the run's loops are. Measured worst case, a tight pure-Python loop: 1–2%, within run-to-run noise. For code that spends its time in numpy or torch, the cost is zero.

It records two things:

- **Executed units:** each code object's `co_qualname` is mapped to its unit, with anything from the first `<…>` segment on belonging to what encloses it. So `f.<locals>.g` maps to `f`, `Model.<lambda>` to `Model`, and module-level lambdas and genexprs to `<module>`.
- **Source snapshots:** each file's text *as it was when the run first loaded it*. If `models.py` is edited halfway through a three-hour run, the record holds the code that ran, and the run warns: `models.py changed while the run was in progress; recorded the code that ran, so vouch check will report this run stale`.

A run records, per first-party file:
- every module and class unit, since they run at import
- the functions it executed

Consequences:

- **Editing a function the run never executed doesn't make it stale.** The run is reported as `cosmetic`, "files changed, but not in anything this run executed", which passes. This is what removes the noise that led asqc to add a separate, non-failing "library drift" tier.
- The staleness report names the unit: `src/models.py::ResNet.forward changed`.

**Where precision isn't safe, files count whole.** The record lists each such file and why, under `code.whole_files`:

| Situation | Reason recorded |
|---|---|
| a module imported before `import vouch` | `imported before vouch started tracking` (its import-time code ran unobserved) |
| the entry script, if anything before `import vouch` could have called first-party code | `code ran before \`import vouch\`` |
| the entry script, if vouch is imported inside a function | `vouch was imported from inside a function` |
| code that ran but can't be found in the source | `executed code not found in the source (…)` |

"Could have called first-party code" is decided from the AST of the statements before `import vouch`. Only calls whose root name is one of the script's own definitions, or a name imported from a first-party module, count, including decorators, default values, class bodies and base classes (applying a decorator or subclassing is a call). Imports, definitions, `sys.path.insert(...)`, `Path(__file__)`, `pd.DataFrame(...)` and other stdlib or third-party calls don't count.

**The whole run falls back to module granularity** (`code.granularity: "module"`, with `code.why`) when:

- the Python version has no `sys.monitoring` (< 3.12)
- no monitoring tool id is free
- `VOUCH_TRACE=0` is set
- `[freshness] granularity = "module"` is configured (no `why` is recorded in this case)
- **the run started Python child processes**, which aren't observed. Detection costs nothing: audit hooks see `os.fork`, and any `CreateProcess`, `posix_spawn` or `subprocess` whose command runs Python. When the record is written, multiprocessing's own process counter is also read, if the experiment imported it. Other programs (`git`, a shell tool) don't count. Neither do vouch's own `git` calls.

An earlier version imported and patched `multiprocessing` at start-up to detect children. That measurably slowed hot loops (~8%), which is why detection is now lazy.

**First-party code** is every `.py` file under the project root (plus `python.first_party`), excluding `python.exclude` and anything inside the interpreter's prefixes and `site-packages`/`dist-packages`. Third-party packages are tracked by version (`env-drift`), not by source.

`vouch trace KEY` shows how each file was tracked (`tracked by function`, or `tracked whole -- <reason>`).

### 8.3 Inputs, artifacts and upstream staleness

- **Inputs** are hashed by content, or by size+mtime with `mode="stat"`. Directories hash as a sorted list of (relative path, hash). A hash cache keyed by (path, size, mtime_ns) in `.vouch/cache/hashes.json` makes repeated checks cheap.
- **Dependency between runs.** If run *B* recorded an input that is an artifact of run *A*, then *B* depends on *A*:
  - If *A*'s recorded artifact hash differs from what *B* recorded as its input, *A* was re-run after *B*, and *B* is **stale** (`input results/a.csv was regenerated by run A`). This is detected even when the file isn't on disk.
  - If *A* is stale, *B* is **upstream-stale**.
- **Artifacts on disk** are checked when present. A mismatch with the recorded hash is `tampered` (the file was edited or regenerated outside a run). A missing file is `absent`, which is only a warning: the committed store alone is enough to check the paper, because values live in the run record. Figures cited by the paper must be present for LaTeX anyway.

### 8.4 Run states

| State | Meaning | Default severity |
|---|---|---|
| `fresh` | every unit, input and artifact matches | ok |
| `cosmetic` | files changed, but not in any unit the run depends on (comments, docstrings, formatting, or functions it never executed) | info |
| `stale` | a unit's semantic hash changed, a unit disappeared, or an input changed | **error** |
| `upstream-stale` | a run this one read from is stale | **error** |
| `tampered` | an artifact on disk differs from its recorded hash | **error** |
| `incomplete` | the record's `status` is not `complete` | **error** |
| `accepted` | stale, but the exact current state was reviewed (§8.5) | info |
| `env-drift` | a recorded third-party package version differs from the current environment | warning |
| `absent` | a recorded input or artifact is not on disk | warning |

### 8.5 Accepting a reviewed staleness

Not every logic change changes a result: a refactor, an added log line, a renamed variable. That judgement belongs to a person, and it is recorded:

```console
$ vouch accept cifar_resnet --why "added a progress bar to ResNet.forward; outputs unchanged"
```

`.vouch/accepted.toml` then records the run, the **exact current hashes** of the changed units and inputs, who (`git config user.name` or `$USER`), when, and why. The acceptance clears the run only for those hashes: the next edit re-opens it. This keeps asqc's ledger semantics.

Accepting is a human action. The agent rules (§13.5) forbid an agent from doing it without the user's approval.

---

## 9. Change notification

A number can be updated correctly and still leave its sentence wrong ("the best of all models", "roughly doubles", "in all five seeds"). A change can also be the first visible sign of a bug. So every change to a **cited** value is treated as pending until someone has reviewed the sentences that cite it.

### 9.1 The acknowledged baseline

`.vouch/acknowledged.json` (committed) stores, for each cited key:

```json
{"schema": "vouch/1", "keys": {
  "cifar.resnet.acc": {"raw": {"mean": 0.93214, "std": 0.0041, "n": 5}, "rendered": ["\\ensuremath{93.2 \\pm 0.4}\\%"], "source": "cifar_resnet", "record_hash": "sha256:3c9e…", "acked": "2026-09-10T12:00:00Z", "by": "Daniel Felps", "why": "first build"}
}}
```

- A key cited for the first time is acknowledged automatically by `vouch build`. You just wrote it, so there is nothing to review.
- `vouch check` is read-only. It reports differences from the baseline but never moves it.

### 9.2 Change classes

`vouch build` and `vouch check` compare each cited key's current state with its baseline:

| Class | Trigger | Default severity |
|---|---|---|
| `changed` | the rendered text differs from the acknowledged text | warning; **error with `--strict`** |
| `suspicious` | a `changed` value that also matches a problem heuristic (below) | warning labeled **POSSIBLE PROBLEM**; **error with `--strict`** |
| `hidden` | the raw value moved, but the printed rounding hides it (asqc's snapshot insight) | info. Reported once by `build`, logged to `history.jsonl`, then acknowledged automatically: the printed text can't have become wrong, but the move may reveal nondeterminism. |
| `reformatted` | only the format changed (the raw value is identical) | info; acknowledged automatically |
| `figure-changed` | a cited figure's artifact hash differs from the acknowledged one (re-read the caption) | warning; **error with `--strict`** |
| `fragile` | a claim still holds, but by less than `changes.claim_margin` | warning |
| `false-claim` | a claim no longer holds | **error** (always) |

**Problem heuristics** (each can be toggled in `[changes]`):

| Heuristic | Fires when |
|---|---|
| sign flip | old and new have opposite signs, or one of them is zero |
| non-finite | the new value is NaN or ±inf |
| large move | `|new − old| / |old| > rel_threshold` (default 10%) |
| order of magnitude | `|log10(new/old)| ≥ 1` |
| sample size | a `Stat`'s `n` changed (seed count changed) |
| provenance moved | the key is now produced by a different run or derivation |
| type changed | e.g. `float` → `stat` |
| direction reversed | for keys with `better`, a comparison-bearing derived value or claim input moved against its `better` direction beyond the threshold |

### 9.3 Where you hear about it

1. **`vouch build`** prints a block per change. It shows old → new, the absolute and relative Δ, the heuristic that fired, and **each citing sentence** with its file:line:

   ```
   2 CHANGED VALUES — re-read the sentences below

     SUSPICIOUS  cifar.vit.acc   91.2\% → 72.4\%   (Δ −18.8 pts, −20.6%; large move)
       main.tex:118  "ViT-B trails ResNet-50 by only \vouch{cifar.resnet_vs_vit.pts} points…"
       main.tex:203  "…ViT reaches \vouch{cifar.vit.acc} (Table~\ref{tab:main})."
       claim cifar.resnet_beats_vit still holds (margin 28.7%)

     SUSPICIOUS  cifar.resnet_vs_vit.pts   2.0 → 20.8   (Δ +18.8, +940%; large move, order of magnitude;
                                                          derived from cifar.vit.acc)
       main.tex:118  "ViT-B trails ResNet-50 by only \vouch{cifar.resnet_vs_vit.pts} points…"

   → fix any sentence that is now wrong, then: vouch ack <key>…  or  vouch review
   ```

2. **`vouch check`** keeps listing pending changes until they are acknowledged, so a notice can't scroll away. With `--strict` (CI, agents, optionally pre-commit) they block.
3. **The PDF** highlights pending values, and their tooltips say what they were (§7.5).
4. **`vouch changes [--json | --md FILE]`** gives the full pending list. `--md` writes a shareable review report, e.g. for co-authors.
5. **The `on_change` hook.** If `changes.on_change` is set, `vouch build` runs that command once per newly detected batch and sends the changes as JSON on stdin (same schema as `vouch changes --json`). Use it for desktop notifications, a Slack webhook, email, and so on. Which changes have already been notified is tracked in `.vouch/cache/notified.json`, so a change never notifies twice.

### 9.4 Review and acknowledgment

```console
$ vouch review                        # interactive: one change at a time
  SUSPICIOUS cifar.vit.acc 91.2\% → 72.4\% …  (sentences shown)
  [a]ck  [s]kip  [o]pen main.tex:118  [d]etails  [q]uit > o      # opens $EDITOR / `code -g`
$ vouch ack cifar.vit.acc --why "bug fix in augmentation; text updated in §4.2"
$ vouch ack --all --why "re-ran all with 5 seeds"
```

Acknowledging moves the baseline and appends an event to `.vouch/history.jsonl` recording the key, old → new, who, when and why. That file is a permanent changelog of the paper's numbers. `vouch ack` and `vouch accept` rebuild the generated files themselves, so highlights and tooltips update without a separate `vouch build`. Acknowledgment is a human action; agents surface changes and fix text, but don't acknowledge without approval (§13.5).

---

## 10. Provenance CSV

The everyday way to see where a number came from is the PDF's provenance appendix (§7.4). The CSV is the same information as a table, for when there is no draft PDF: a final (double-blind) submission's supplementary material, artifact evaluation, a spreadsheet, or a script. `vouch export --csv PATH` writes it on demand. Setting `provenance_csv` under `[[paper]]` makes every build write it too; it is then committed and checked for `out-of-sync` like the other generated files.

It has **one row per key the paper cites**, in reading order (order of first citation). Figures cited through `\includegraphics` get rows too. It is deterministic, so a committed copy's diff is readable.

| Column | Content |
|---|---|
| `key` | the cited key |
| `kind` | `value` · `claim` · `derived` · `param` · `table-cell` · `table` · `figure` · `pending` |
| `rendered` | every rendered form cited, joined with ` \| ` |
| `raw_value` | the raw value (a JSON number, or JSON for Stat and tuple); for a claim, `true`/`false` plus the values it read |
| `fmt`, `unit`, `description`, `better` | as recorded |
| **`experiment`** | the run id, or `derive:vouch_values.py::<function>` for derived values |
| `script` | the run's entry script, or the values module |
| `call_site` | file:line of the `record()` call or the definition |
| `command` | the exact command that produced the run |
| `params` | the run's parameters, as JSON |
| `inputs` | input paths with short hashes; for derived values, the keys read |
| `git_commit`, `git_dirty` | at record time |
| `recorded_at`, `duration_s` | from the run |
| `freshness` | a §8.4 state |
| `change_status` | `acked` · `changed` · `suspicious` · `hidden` · `new` |
| `previous_value`, `acked_at` | from the baseline |
| `cited_at` | every tex file:line, joined with `;` |

`vouch export --csv PATH [--all] [--json]` writes the same table on demand. `--all` also includes keys that are recorded but not cited.

There is exactly one CSV per paper, covering every cited key. This was decided in review; no per-key files.

---

## 11. Checks

`vouch check` runs every check below. It never executes experiments or user code (§5.6).

- **Exit codes:** `0` pass, `1` fail, `2` could not check (unreadable config, unparseable tex, no store). This is asqc's convention.
- **Severities:** `error` always fails. `warning` fails under `--strict`. `info` never fails. Override per check with `[check] severity`.

| Check | Default | Trigger | Fix |
|---|---|---|---|
| `config` | error | invalid `vouch.toml` or tex graph | message names the file and line |
| `store-edited` | error | a run record's or `derived.json`'s `record_hash` doesn't match its content | re-run the experiment (or `vouch build`); never hand-edit `.vouch/` |
| `derive-error` | error | a values module failed to import, or a definition raised, read an unknown key, read a failed definition, returned the wrong type, or was defined twice; the message names the file:line | fix the definition, then `vouch build` |
| `unknown-key` | error | a cited key exists nowhere (did-you-mean suggestions included) | fix the key, or `record`/`derive`/`expect` it |
| `key-conflict` | error | two sources produce one key | rename one |
| `out-of-sync` | error | generated files or `derived.json` differ from what `build` would write. Run freshness is excluded from the comparison: it depends on the working tree, not on what was recorded, so it lives on separate `\vouch@state` lines (read by tooltips) and in the CSV's freshness column. A code edit is reported once, as `stale`, never also as `out-of-sync`. | `vouch build` |
| `stale` / `upstream-stale` | error (info when the paper cites nothing from the run) | §8.4 | re-run (exact command shown) or `vouch accept` |
| `tampered` / `incomplete` | error | §8.4 | re-run |
| `untracked-input` | error | a derivation reads a file no run produced and not listed as `external` | produce it in a run, or declare it `external` |
| `alias-target` | warning | an alias names a key under which nothing is recorded | fix the alias |
| `derive-cycle` | error | derived values depend on each other | break the cycle |
| `false-claim` | error | a claim no longer holds; the message shows the values it read | re-examine the result and the prose |
| `figure-stale` | error | a cited figure's producing run is stale | re-run |
| `changed` / `suspicious` / `figure-changed` | warning | §9.2 | re-read the cited sentences, then `vouch ack` |
| `fragile` | warning | a claim holds by less than `changes.claim_margin` (§5.3) | soften the claim, or check it is not noise |
| `pending` | warning | a cited `expect()` key has no producing run | run the `producer` command |
| `no-source` | warning | a bare number in prose that matches **no** recorded value: the signature of an invented number | find the real value (`vouch search`) or remove the number |
| `bare-number` | warning | a bare number that **does** match a recorded value | `vouch suggest --apply` |
| `figure-untracked` | warning | an `\includegraphics` with no producing run | save it inside a run |
| `no-description` | warning | a cited key has no `desc` | add `desc=` |
| `env-drift` / `absent` | warning | §8.4 | re-run if the difference matters |
| `imported` | info (warning without `--producer`) | a run was registered with `vouch import`, not recorded live | re-run the producer under vouch for full provenance |
| `cosmetic`, `accepted`, `hidden`, `reformatted`, `unused-value`, `expectation-met`, `default-float-format`, `dirty-tree-at-record` | info | — | — |

### 11.1 The bare-number lint

Ported from asqc's `check.lint` and extended:

- **Scope:** the document body (after `\begin{document}`), with comments, vouch macro arguments and LaTeX plumbing masked. Plumbing includes the arguments of `\label`, the `\ref`/`\cref`/`\autoref`/`\eqref` family and `\cite*`, URLs, `\includegraphics` options, lengths and units (`0.5\linewidth`, `3pt`, `2em`), tabular column specs, and `\begin`/`\end`.
- **Always flagged**, including in math: decimals (`0.93`), percentages (`12.5\%`), `\times` multipliers after a number, thousands separators (`18{,}535`), scientific notation (`1.2\times10^{-3}`), and `\pm` pairs.
- **Flagged in text mode:** integers of two or more digits (`38 layers`). Years (`19xx`/`20xx`) are skipped when `allow_years` is on. Single digits and spelled-out numbers are never flagged.
- **Exemptions:** `[lint] allow` rules (a regex matched against ±60 characters of context, plus a reason, optionally limited to certain files), and a `% vouch: ignore` pragma on the line.
- **Matching** (this powers `bare-number` vs `no-source` and `vouch suggest`). Each flagged literal is parsed together with its implied precision (asqc's `parse_printed_full`: `93.2\%` claims 1 decimal place). It is then compared against every recorded value and its common transforms (×100 for percentages, Stat mean and std, tuple elements, params). A match is exact when rounding the candidate to the literal's implied precision reproduces the literal.

---

## 12. CLI reference

Every command accepts `--json`, which emits a stable, versioned envelope (§13.6), and `--root DIR`. Output uses ✓/✗ when the console supports them and falls back to ASCII otherwise.

| Command | Does |
|---|---|
| `vouch init [--paper FILE] [--hook] [--agents]` | Writes `vouch.toml`, detects the main `.tex`, copies `vouch.sty`, adds `.gitignore` entries (`.vouch/cache/`), and prints the `\usepackage` line to add. `--hook` installs the pre-commit hook; `--agents` installs the LLM layer (§13.5). |
| `vouch build [--no-notify]` | Evaluates `vouch_values.py`, renders the values file, tables (and the provenance CSV if `provenance_csv` is set) and catalog, refreshes annotations if enabled, auto-acknowledges new, `hidden` and `reformatted` changes, and prints the change block (§9.3). |
| `vouch check [--strict] [--quiet] [--json] [--paper FILE]` | The gate (§11). Read-only. Target: under 1 s. |
| `vouch status` | Freshness per run, git-status style, with the exact re-run command and the units that changed. |
| `vouch ls [PATTERN] [--cited\|--uncited] [--fields …]` | Keys with rendered value, description, run, freshness and citation count. |
| `vouch trace KEY \| SCRIPT \| FILE:LINE` | The full chain for a key; the values a script produces and where they're cited; or the values cited on a line of tex. |
| `vouch explore [--port N] [--open] [--html FILE] [--json]` | Browse every recorded value in a local web page, grouped by script and function, and copy the LaTeX that cites it (§12.2). |
| `vouch search "WORDS" [--limit N]` | Ranked lookup (§13.2). |
| `vouch cite KEY [--fmt F]` | The snippet to paste, plus its rendering (§13.2). |
| `vouch compare A B [--write]` | Arithmetic, plus ready-to-paste `derive`/`claim` code; `--write` appends it to `vouch_values.py` (§13.2). |
| `vouch suggest [FILE] [--apply]` | Match bare numbers to keys; `--apply` rewrites unique exact matches (§13.2). |
| `vouch todo` | Pending `expect()` keys with their producer commands. |
| `vouch changes [--json\|--md FILE]` | Pending changes to cited values, with the citing sentences. |
| `vouch review` | Interactive review of pending changes. |
| `vouch ack KEY… \| --all \| --run RUN [--why TEXT]` | Acknowledge changes. |
| `vouch accept RUN --why TEXT` | Record a reviewed staleness (§8.5). |
| `vouch export --csv PATH [--all] [--json]` | Provenance table on demand. |
| `vouch catalog` | Regenerate `.vouch/CATALOG.md`. |
| `vouch sync [--strip]` | Refresh or remove source annotations. |
| `vouch run ID [--dep P]… [--input P]… [--out P]… [--values FILE] -- CMD…` | Record a run of any command (§14). |
| `vouch import FILE --run ID [--prefix P] [--producer PATH]… [--command TEXT] [--row-key C] [--stats]` | Register every value in an existing results file with one command (§14.1). |
| `vouch hook install [--strict]` / `vouch hook claude` | Install the git pre-commit hook / the Claude Code hook entry point (§13.5). |
| `vouch mcp` | Start the MCP server (optional extra, §13.7). |

### 12.1 Sample outputs

```console
$ vouch status
runs: 4 fresh · 1 stale · 1 accepted
  ✓ cifar_resnet     fresh        2026-09-12 · 57 min
  ✗ cifar_vit        stale        src/models/vit.py::ViT.forward, src/data.py::augment changed
                                  re-run: python experiments/train.py --model vit
  ~ figures          accepted     "renamed axis label variable" (2026-09-14, Daniel Felps)
```

```console
$ vouch trace cifar.resnet.acc
cifar.resnet.acc = Stat(mean=0.93214, std=0.0041, n=5)   → "93.2 ± 0.4\%"   (fmt .1pct)
  desc      top-1 test accuracy on CIFAR-10, mean ± std over seeds   · better: higher
  recorded  experiments/train.py:88   in run cifar_resnet
  command   python experiments/train.py --model resnet50 --seeds 5
  when      2026-09-12 14:03 UTC · 57 min · git 0fdc530 (clean) · python 3.13.5, torch 2.5.1
  code      14 units in 4 files · fresh            (--code to list)
  inputs    data/cifar10.npz · fresh
  feeds     cifar.resnet_vs_vit.pts (derived) · claim cifar.resnet_beats_vit · table main[resnet, CIFAR-10]
  cited     paper/main.tex:41, paper/main.tex:118, paper/sections/results.tex:22
```

### 12.2 Exploring the registry: `vouch explore`

Keys made by `@vouch.track` are generated from the function and its arguments, so a person writing the paper needs a way to find them without reading JSON. `vouch explore` serves a read-only page on `http://127.0.0.1:8765/`, grouped the way the code is:

```
experiments/train.py        run experiments.train · fresh · python experiments/train.py · git 0fdc530
  evaluate   @vouch.track   experiments/train.py:7
    evaluate.cifar.resnet.lr_0_001.acc   92.9 ± 0.6%   top-1 test accuracy   cited 1×   [\vouch]
      .mean 92.9%  .std 0.6%  .n 5  .ci95 92.2–93.6%  .min  .max                       (each copies its key)
  main       recorded       experiments/train.py:18
    cifar.n_test   10000   test images   not cited   [\vouch]
    paper/figs/curves.pdf   figure saved by the run   [\includegraphics]
  parameters
vouch_values.py
  gap        derived        vouch_values.py:6
  aliases
```

- **Grouping.**
  - **Scripts:** each run's entry script comes first, then `vouch_values.py`.
  - **Tracked values:** a value from `@vouch.track` or `[[track]]` sits under its function.
  - **Recorded values:** a `vouch.record`/`record_all`/`table`/`claim` call, or a saved figure, sits under the function the call is in (found from the source), or under "top level".
  - **Parameters:** these form their own group.
  - **Derived values:** these sit under their definition. Aliases are listed separately.
  - **Order:** groups follow source order.
- **Copying.** Every row has a button that copies its citation:
  - `\vouch{key}` for a value.
  - `\vouchclaim{key}{desc}` for a claim.
  - `\vouchtable{key}` for a table, plus a second button that copies a whole booktabs `tabular` around it with the column headers.
  - `\includegraphics[width=\linewidth]{…}` for a figure, with the path relative to the paper.

  Clicking a subfield chip (`.mean`, `.std`, …) or a table cell copies that key.
- **Details.** Clicking a row opens its provenance:
  - the description, format and unit
  - for tracked values: the call (`evaluate(dataset=cifar, model=resnet, lr=0.001) over seed=0..4 (5 calls)`), each call's result, and the definition and call sites
  - for derived values: the definition, the keys it read (each one a link) and its runs
  - what an alias points to
  - where the key is cited in the paper
- **Finding.**
  - A search box matches keys, descriptions, function names, call arguments (`model=resnet`) and values. Press `/` to focus it.
  - Filters show all, cited, or not-cited keys.
  - Each row shows its run's state (fresh, stale, …) and how often the paper cites it.
- **Links.** `?q=WORDS` opens the page with a search, and `?open=KEY` opens it on one key, expanded.
- **Live.** The page checks every two seconds whether anything it shows has changed: the run records, `derived.json`, the acknowledgment and accept files, `vouch.toml`, or the paper's `.tex` files. Re-running an experiment, or citing a key, updates it in place. Problems a check would report about derived values (out of date, definition errors) appear in a banner.
- **Safety.**
  - The server binds to 127.0.0.1 only and serves only the page and its JSON.
  - It rejects requests whose `Host` isn't the local address, so a web page can't read it through DNS rebinding.
  - It never runs project code: derived values come from `derived.json`, exactly as in `vouch check`.
- **Snapshots.** `--html FILE` writes the same page as one self-contained file with the data embedded, for sharing with a co-author or attaching to a review. `--json` prints the data the page shows.

---

## 13. LLM interface

The rule vouch is built around: **at every point where an agent would otherwise guess, make the correct action cheaper than the guess.**

| Where an agent would guess | vouch gives it instead |
|---|---|
| "what was the accuracy again?" | `CATALOG.md`, `vouch search`, `vouch cite` |
| writing the difference or ratio of two numbers | `vouch compare` → a `derive` |
| "A outperforms B" | `vouch compare` → a claim; `\vouchclaim` |
| a number that doesn't exist yet | `vouch.expect()` → `[pending: key]` |
| converting an existing paper | `vouch suggest --apply`, with `no-source` for numbers matching no record |
| knowing whether it slipped | the edit hook (immediate) and `check --strict --json` (a fix list) |
| a value changed under its prose | `vouch changes`, with the citing sentences |

### 13.1 The catalog: `.vouch/CATALOG.md`

Written by every build and committed, the catalog lets an agent see everything citable with a single file read and no command. It is kept token-lean: one line per key, empty fields omitted.

```markdown
# vouch catalog — 38 values · 3 claims · 1 table · 1 pending · 2 changed
Cite: \vouch{key} · \vouch[fmt]{key} · \vouchclaim{key}{text} · \vouchtable{key}
Formats: .1f .2e ,d .1pct x=\times u=unit · Stat → mean \pm std · subfields .mean .std .n .ci95
Find: vouch search "words" · Snippet: vouch cite KEY · Arithmetic/claims: vouch compare A B · Missing: vouch.expect(...)
Never type a number. Never compute with numbers in prose.

## cifar (9)
cifar.resnet.acc · 93.2 ± 0.4% · top-1 test accuracy, mean ± std over seeds · higher↑ · cifar_resnet · fresh · cited 3×
cifar.vit.acc · 72.4 ± 1.1% · top-1 test accuracy, mean ± std over seeds · higher↑ · cifar_vit · CHANGED · cited 2×
cifar.resnet_vs_vit.pts · 20.8 points · ResNet minus ViT top-1, percentage points · derived · cited 1×
cifar_resnet.param.epochs · 100 · param · cifar_resnet
…
## claims
cifar.resnet_beats_vit · HOLDS (margin 28.7%) · ResNet top-1 > ViT top-1
## tables
main · 3×3 · Model | CIFAR-10 | ImageNet · cells main.<model>.<column>
## pending (vouch todo)
imagenet.convnext.acc · ConvNeXt-T top-1 on ImageNet · run: python experiments/train.py --dataset imagenet --model convnext
## changed since last ack (vouch changes)
cifar.vit.acc · 91.2% → 72.4% · SUSPICIOUS (large move) · main.tex:118, main.tex:203
```

Projects with more than 300 keys get an index in `CATALOG.md` (prefix, count, one-line summary) and one file per prefix under `.vouch/catalog/`.

### 13.2 Lookup and insertion commands

**`vouch search`** ranks keys with BM25 over a document per key: key segments (split on `.`, `_`, `-` and camelCase), description, run id, unit, parameter names and table names. A small built-in synonym list (acc/accuracy, lr/learning rate, std/deviation, n/seeds/samples, …) helps. It is stdlib-only, with no embeddings.

```console
$ vouch search "vit accuracy cifar"
cifar.vit.acc            72.4 ± 1.1%   top-1 test accuracy, mean ± std over seeds   (cifar_vit, CHANGED)
cifar.vit.acc.std        1.1%          …std subfield
cifar.resnet_vs_vit.pts  20.8          ResNet minus ViT top-1, percentage points    (derived)
```

**`vouch cite`** returns the exact snippet, and enough context to write the sentence correctly:

```console
$ vouch cite cifar.resnet.acc
\vouch{cifar.resnet.acc}          →  93.2 ± 0.4\%    (default fmt .1pct)
\vouch[.2pct]{cifar.resnet.acc}   →  93.21 ± 0.41\%
top-1 test accuracy on CIFAR-10, mean ± std over seeds · higher is better · fresh · run cifar_resnet
subfields: .mean 93.2\% · .std 0.4\% · .n 5 · .ci95 [92.7, 93.7]
```

**`vouch compare`** does the arithmetic and writes the code that makes it citable. For two Stats it also reports the difference in pooled standard deviations and, if SciPy is installed, a Welch t-test p-value. An agent can then write "significantly" only when that is true.

```console
$ vouch compare cifar.resnet.acc cifar.vit.acc
cifar.resnet.acc 93.2% vs cifar.vit.acc 72.4%   (higher is better → cifar.resnet.acc is better)
  difference +0.208 (20.8 points) · ratio 1.287 · relative +28.7%
  Stat: 25 pooled std apart · Welch t-test p < 1e-6 (n = 5, 5)
paste into vouch_values.py (or run again with --write):
  @vouch.derive("cifar.resnet_vs_vit.pts", fmt=".1f", unit="points", better="higher",
                desc="cifar.resnet.acc minus cifar.vit.acc, percentage points")
  def _(v): return 100 * (v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"])

  @vouch.claim("cifar.resnet_beats_vit", desc="cifar.resnet.acc > cifar.vit.acc")
  def _(v): return vouch.gt(v["cifar.resnet.acc.mean"], v["cifar.vit.acc.mean"])
then cite:
  \vouchclaim{cifar.resnet_beats_vit}{ResNet-50 outperforms ViT-B} by \vouch{cifar.resnet_vs_vit.pts} points
```

**`vouch suggest`** converts numbers already written in the paper, and exposes invented ones:

```console
$ vouch suggest paper/main.tex
main.tex:88   93.2\%    → \vouch{cifar.resnet.acc.mean}      exact (fmt .1pct)
main.tex:90   0.93      → \vouch[.2f]{cifar.resnet.acc.mean}  exact
main.tex:131  12.5\%    → NO SOURCE — no recorded value renders as 12.5% (nearest: cifar.vit.drop = 12.1%)
main.tex:140  3{,}014   → ambiguous: compl.words (3014), sweep.n_configs (3014) — choose by hand
4 literals: 2 replaceable (--apply), 1 no-source, 1 ambiguous
```

`--apply` rewrites only unique exact matches and keeps any surrounding math delimiters. Ambiguous and no-source literals are never touched.

### 13.3 Placeholders instead of invention

When an agent drafting prose needs a number that doesn't exist, the approved move is:

1. Write `\vouch{imagenet.convnext.acc}` in the prose.
2. Add `vouch.expect("imagenet.convnext.acc", desc=…, producer="…")` to `vouch_values.py`.
3. Tell the user the experiment is owed. `vouch todo` lists it.

The draft compiles with a visible `[pending: …]`. This gives the agent a legitimate move other than inventing a number, which is the most important anti-hallucination feature in this spec.

### 13.4 While writing experiment code

The skill (§13.5) teaches this pattern, and `record()`'s warnings enforce it:

- keys follow `<dataset>.<model>.<metric>`; one run id per configuration (`f"{dataset}_{model}"`)
- always a `desc`, and `better=` for any metric
- multi-seed results use `Stat.of(per_seed)` (or `record_all(..., stats=True)`), never a hand-computed mean
- prefer one `record_all(metrics, prefix=…)` at the end of an experiment over many scattered `record()` calls, and add a `[metrics]` pattern in `vouch.toml` for any new metric name rather than repeating `desc`/`fmt` in code
- data goes through `run.input()`, outputs through `run.artifact()`; figures are saved inside the run
- parameters go through `params=`, so hyperparameters in the paper are citable

`vouch status --json` gives an agent the stale runs and their exact re-run commands. `vouch todo --json` gives the owed experiments.

### 13.5 Claude Code integration (`vouch init --agents`)

Each of the three parts is optional, and the command shows a diff before writing anything.

**1. Skill: `.claude/skills/vouch/SKILL.md`.** It loads only when relevant, so it costs no context otherwise. Its description triggers on editing `.tex` in a vouch project, writing experiment code that produces results, or mentions of results, numbers, tables or claims. It has four workflows:

- *Record results in an experiment* (§13.4)
- *Write a results paragraph*: search → cite → compare → claim → check
- *Handle changed values*: `vouch changes` → re-read each sentence → fix the text → report suspicious changes → ask before acking
- *Convert an existing paper*: `suggest` → `--apply` → resolve `no-source` with the user

It ends with a command cheat-sheet.

**2. Rules block** appended to `CLAUDE.md` (or `AGENTS.md`), between `<!-- vouch -->` markers so it can be updated in place:

```markdown
<!-- vouch -->
## Numbers in the paper (vouch)
- Never type an empirical number into LaTeX. Find it (`vouch search`, `.vouch/CATALOG.md`), then paste `vouch cite KEY`.
- If it doesn't exist: `record()` it in the experiment, `@vouch.derive` it, or `vouch.expect()` it and tell the user.
- Never compute with numbers in prose (differences, ratios, "2x"): `vouch compare A B --write`, then cite the derived key.
- Qualitative comparisons ("outperforms", "all seeds") go in `\vouchclaim` backed by a claim.
- Before finishing: `vouch check --strict` must pass.
- If `vouch changes` lists anything: re-read each cited sentence, fix wrong text, and report SUSPICIOUS changes to the user.
- Never run `vouch ack` or `vouch accept` without the user's approval. Never edit `.vouch/` or generated files.
<!-- /vouch -->
```

**3. PostToolUse hook** in `.claude/settings.json`:

```json
{"hooks": {"PostToolUse": [{"matcher": "Edit|Write|MultiEdit",
  "hooks": [{"type": "command", "command": "vouch hook claude"}]}]}}
```

`vouch hook claude` reads the hook payload from stdin and ignores anything that isn't a `.tex` file in a configured paper. For those, it checks just the edited file for `unknown-key`, `bare-number`/`no-source` (with suggestions), `pending` and malformed macros. If it finds problems, it prints them with their fixes to stderr and exits `2`, which feeds them back to the agent in the same turn, so a mistake is corrected immediately instead of at commit time.

**Budget:** under 200 ms. It is stdlib-only and reads a precomputed index, `.vouch/cache/index.json`, which `build` writes.

An optional `--stop-gate` also installs a `Stop` hook running `vouch check --strict --quiet`. It is off by default, because blocking an agent from finishing is intrusive.

### 13.6 JSON output (`schema: vouch/1`)

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

- **Issue order** is the order to fix them in, so an agent can loop on `vouch check --strict --json` until it exits 0:
  1. config
  2. `store-edited`
  3. `unknown-key`
  4. `out-of-sync`
  5. stale runs
  6. false claims
  7. changes
  8. pending
  9. lint
- **Fix kinds:** `command` (run this); `edit` (file, line, old text → new text); `build` (run `vouch build`); `human` (needs the user: ack, accept, or a judgement).
- **Compatibility:** field names are stable within `vouch/1`; additions are allowed, removals need `vouch/2`. The schema ships as `vouch/schema/v1.json` and is tested.

### 13.7 MCP server (optional extra `vouch[mcp]`)

`vouch mcp` exposes the same functions to any MCP client, built on the official `mcp` Python SDK. The tools are read-only, except that `compare` may write the definition when asked:

| Tool | Returns |
|---|---|
| `search_values(query, limit=10)` | ranked keys, as in `vouch search` |
| `get_value(key)` | the full record, as in `vouch trace --json` |
| `cite(key, fmt=None)` | the snippet and its rendering |
| `compare(a, b, write=False)` | arithmetic plus derive/claim code |
| `list_pending()` | `vouch todo` |
| `list_changes()` | `vouch changes` |
| `check(strict=True)` | the §13.6 envelope |
| `trace(target)` | `vouch trace` |

Resource: `vouch://catalog` (the catalog). `ack` and `accept` are deliberately **not** exposed: acknowledgment and acceptance stay human actions.

---

## 14. Non-Python experiments: `vouch run`

```console
$ vouch run cifar_vit_jl --dep src/ --dep configs/vit.yaml --input data/cifar10.npz \
      --out results/vit.csv -- julia train.jl --model vit
```

1. **Before running**, vouch hashes `--dep` paths (semantically for `.py`, by raw content otherwise; directories recursively, honoring `python.exclude`) and `--input` paths.
2. It then runs the command with `VOUCH_RUN`, `VOUCH_ROOT` and `VOUCH_VALUES` (a temporary JSON path) set in the environment.
3. The program writes its values to `$VOUCH_VALUES`, either as full records or as shorthand:

   ```json
   {"values": {"cifar.vit.acc": {"value": 0.912, "fmt": ".1pct", "desc": "ViT top-1", "better": "higher"}},
    "claims": {}, "artifacts": ["results/vit.png"], "tables": {}}
   ```
   ```json
   {"cifar.vit.acc": 0.912}
   ```

4. **On exit 0**, vouch hashes `--out` paths and declared artifacts, and writes a run record with `code.granularity = "deps"`. On a non-zero exit it writes nothing and passes the exit code through.
5. **Python commands.** When the command is `python script.py …`, vouch runs it as `python -m vouch.exec script.py …`. The script gets function-level tracking with no code changes, and its own `vouch.record()` calls attach to this run.
6. **Existing results files.** `vouch run … --values results.json` reads values from a file the program already writes, instead of `$VOUCH_VALUES`. It accepts the same shapes as `record_all` (nested JSON, JSONL or CSV, plus `--prefix`, `--row-key` and `--stats`). A program that already dumps its metrics needs no changes at all.

### 14.1 Registering results that already exist: `vouch import`

Results sometimes come from code that has already run: before you adopted vouch, from a notebook, or from a collaborator's cluster job. `vouch import` registers the whole file in one command, with honest, reduced provenance:

```console
$ vouch import results/imagenet_eval.json --run imagenet_eval --prefix imagenet \
      --producer experiments/eval_imagenet.py --producer src/models/ \
      --command "python experiments/eval_imagenet.py --split val"
imported 24 values into run imagenet_eval (prefix imagenet) · granularity: declared (3 files)
  metadata from [metrics]: 24/24 described
  note: imported, not recorded live; freshness tracks the declared producer files
```

- **Input shapes:** the same as `record_all`: JSON (nested), JSONL, CSV (`--row-key`), and per-seed lists (`--stats`).
- **Provenance:**
  - The run record's `code.granularity` is `declared`: the `--producer` files and directories are hashed semantically, so editing them makes the run stale, as with any other run.
  - The results file itself is recorded as an input, so editing it by hand is detected.
  - `command` holds whatever `--command` says, marked as *declared rather than observed*.
- **Honesty in every view:**
  - `trace`, the CSV (`experiment` column: `imagenet_eval (imported)`) and the tooltips all say the values were imported.
  - `check` reports `imported` as info, and as a warning when there is no `--producer`, because then nothing ties the numbers to code.
  - Re-running the producer under a normal run replaces the imported record.

---

## 15. Workflow integration

- **Git.**
  - Commit everything except `.vouch/cache/`.
  - `vouch init` adds `.gitattributes` entries marking generated files `linguist-generated=true`.
  - After a merge conflict in a generated file, take either side and run `vouch build`.
  - Run records are one file per run id, so parallel experiment branches rarely conflict.
- **Pre-commit.**
  - `vouch hook install` writes `.git/hooks/pre-commit`, honoring `core.hooksPath`, with the same venv detection as asqc's `.githooks/pre-commit`. It runs `vouch check --quiet`, plus `--strict` if `[hook] strict`.
  - The vouch repo also ships a `.pre-commit-hooks.yaml` (`id: vouch-check`) for the pre-commit framework.
  - Following asqc's lesson, the hook must stay fast enough that nobody reaches for `--no-verify`.
- **CI.** `pip install vouch && vouch check --strict`. The README includes a GitHub Actions example.
- **Overleaf.**
  - Everything LaTeX needs lives in the paper directory: `vouch.sty`, the values file and the tables. These sync through Overleaf's git bridge.
  - Co-authors editing on Overleaf cite existing keys (from the committed catalog); unknown keys show `??key`.
  - The build and check run locally or in CI.
- **arXiv and camera-ready.** Use `\usepackage[final]{vouch}` and upload `vouch.sty` plus the generated files. `final` loads no extra packages and leaks no paths.
- **Clusters.** Records are plain files. Copy `.vouch/runs/<id>.json` (and any artifacts) back from the cluster. Set `VOUCH_ROOT` if jobs run from another working directory. Paths are always stored relative to the project root.

---

## 16. Integrity, performance, compatibility

### 16.1 Integrity

- **`record_hash`** detects accidental or naive edits to run records, including an agent "fixing" a number in the JSON. It is **not** tamper-proof: anyone can recompute it. Git history is the real audit trail. Signing records with an SSH key (git's allowed-signers format) is noted for v2.
- **`out-of-sync`** detects edits to generated files.
- **Honest trace.** A run recorded from a dirty working tree gets an `info` note. Its code hashes still verify, but its git commit does not contain that code, and the trace says so rather than implying otherwise.

### 16.2 Performance targets

| Operation | Target |
|---|---|
| `vouch check` (500 cited values, 50 runs, warm hash cache) | < 1 s |
| `vouch hook claude` | < 200 ms |
| tracking overhead during an experiment | < 1% (one callback per distinct function) |
| `record()` | O(1), no I/O until the run finalizes |
| `vouch build` with a warm derive cache | < 2 s |

### 16.3 Compatibility

- **Python:** 3.10+. `tomli` is needed on 3.10; function-level tracking needs 3.12+. Developed on 3.13.
- **Operating systems:** Windows, macOS, Linux. Paths are always stored POSIX-style.
- **LaTeX:** pdfLaTeX, LuaLaTeX and XeLaTeX. Tested with the article, IEEEtran and acmart classes and the NeurIPS/ICML style files; `pdfcomment` tooltips need verification per engine (§20).

---

## 17. Prior art

| Tool | What it does | How vouch differs |
|---|---|---|
| showyourwork | `\variable{file}` from Snakemake rules; rebuilds everything, focused on astronomy | vouch records values at run time, never re-executes, and hashes at the function level |
| DVC | data and pipeline versioning | no link to the paper's numbers |
| MLflow, W&B, Sacred | experiment tracking | no citation, freshness or paper check |
| knitr, Quarto, pythontex | code executes inside the document build | couples the paper build to the compute environment; vouch keeps them apart |
| asqc `audit/` | this project's direct ancestor | generalizes it and removes most of its ceremony (§18) |

---

## 18. Lessons carried over from asqc

| asqc lesson | vouch design |
|---|---|
| a registry entry and extractor per number is heavy | values are recorded where computed; `derive` only for real computations |
| git ancestry needed `--promote` and a non-failing library tier | content hashing plus function-level units |
| docstring edits must not fail the gate (`cosmetic_since`) | semantic hashing, with `cosmetic` as info |
| a slow hook provokes `--no-verify` | check < 1 s; the hook runs no experiments |
| a value moving within printed rounding is invisible | the `hidden` change class (reported, logged) |
| one-entry-per-line JSON makes diffs reviewable | every committed file is deterministic and line-per-entry |
| exit code 2 = could not check | same convention |
| a key hoisted into an unused `\newcommand` looked "printed" | macro-aware citation scanning (§7.7) |
| writing tex through Python string literals mangles `\v`, `\t`, `\b` | the emitter uses raw templates only; a test round-trips `\vouch`, `\times`, `\text`, `\begin`, `\frac`, `\alpha`, `\newcommand` |
| a filter keyed on the package name silently stopped tracking | first-party detection is by path, and a test asserts nonzero units are tracked |
| an accepted staleness should re-open on the next edit | the accept ledger stores exact hashes |

---

## 19. Implementation plan

### 19.1 Package layout

```
src/vouch/
  __init__.py        public API: run, record, claim, input, artifact, table, params, expect, Stat,
                     derive, claim (decorator), table (decorator), gt/ge/lt/le/between/approx
  api.py             Run, the implicit run, finalize, validation
  store.py           records: canonical JSON, deterministic writer, record_hash
  hashing.py         semantic unit hashes, file/dir hashes, hash cache
  units.py           AST → units (module/class/function), qualname resolution
  tracing.py         sys.monitoring tracking, multiprocessing/fork detection, module fallback
  fmt.py             format grammar → LaTeX, rounding, Stat/tuple/unit/siunitx
  derive.py          values-module evaluation, accessor, cache, derived.json
  freshness.py       run states, dependency propagation, accept ledger
  changes.py         baseline, change classes, heuristics, history, on_change
  provenance.py      CSV export
  catalog.py         CATALOG.md + index.json
  search.py          BM25 + synonyms
  match.py           literal ↔ value matching (suggest, no-source)
  check.py           issue model, severities, ordering, report, JSON envelope
  cli.py             argparse subcommands
  exec.py            python -m vouch.exec
  hooks.py           git pre-commit install, `vouch hook claude`
  mcp_server.py      optional
  tex/scan.py        file graph, comment masking, citations, macro awareness, sentences
  tex/lint.py        bare-number lint
  tex/emit.py        values file, tables, annotations
  data/vouch.sty  data/SKILL.md  data/agents_block.md  schema/v1.json
tests/  examples/minimal/
```

Tooling: uv, pytest, argparse (no click, to keep the core dependency-free). Optional extras: `vouch[pandas]` and `vouch[mcp]`.

### 19.2 Milestones

| # | Milestone | Done when |
|---|---|---|
| M1 | **Recording**: store, semantic hashing, `run`/implicit run, values, `record_all` with `[metrics]` defaults, `Stat`, params, inputs, artifacts, atomic finalize | records are deterministic and round-trip; a crash writes nothing; a nested metrics dict registers in one call, fully described |
| M2 | **LaTeX**: `vouch.sty` (tooltips, `final`, pending/unknown markers), scanner, formatter, `build`, `init`, provenance CSV, `export` | the example compiles with values and tooltips; the CSV matches its golden file |
| M3 | **Freshness and changes**: module granularity, `check`, `status`, `trace`, `ls`, `accept`, exit codes, `hook install`; baseline, change classes, heuristics, build block, `changes`, `review`, `ack`, `on_change`, highlighting | editing code fails the check and names the file; a moved value is surfaced and acknowledged |
| M4 | **Function-level tracking**: `sys.monitoring`, unit resolution, child-process fallback, changed-unit reporting; `@vouch.track`, `[[track]]` and `python -m vouch.exec` | editing an unexecuted function stays fresh; editing an executed one names the unit; an unmodified script under `vouch.exec` records its `[[track]]` functions |
| M5 | **Claims, derived values, tables, figures**: values-module evaluation and caching, `derived.json`, claim helpers and `fragile`, tables with highlights and cell keys, the savefig hook, `\includegraphics` checks, `vouch.alias` | the example's main table, derived gain and claim work end to end |
| M6 | **Lint, annotations, wrapper**: lint port, `sync`, `vouch run` (incl. `--values`), `vouch import`, README quickstart | lint fixtures pass; a shell example records; an existing results JSON imports with declared provenance |
| M7 | **LLM layer**: `expect`/`todo`, `search`, `cite`, `compare`, `suggest --apply`, `no-source`, catalog, JSON schema and `fix` everywhere, `init --agents` (skill, rules block, hook) | the agent dry run (§19.3) passes |
| M8 | **MCP server** | tools callable from Claude Code |
| M9 | *(optional)* **Dogfood** on a real paper, e.g. `quantum-nas/paper/structural_filter.tex` | the paper passes `check --strict` |

### 19.3 Testing

- **Unit tests:**
  - **Semantic hash:** a comment or docstring edit is cosmetic; a logic edit is not; moving a function doesn't change its unit hash.
  - **Units:** `<locals>` collapse, class/module stubbing, duplicate qualnames.
  - **Tracking:** an unexecuted function stays fresh; an executed one is stale and named; the multiprocessing fallback triggers.
  - **Formatting:** a golden table covering every row of §6.2, plus rounding edge cases (`0.125`, `2.675`, negatives, `-0.0`).
  - **Scanner:** comments, `\%`, the `\input` graph, optional arguments, macro-definition awareness, sentence extraction.
  - **Lint:** asqc's allowlist cases as fixtures; `bare-number` vs `no-source` matching.
  - **Store:** determinism, `record_hash` tamper detection.
  - **Freshness state machine:** input changed, upstream propagation, regenerated artifact, accept followed by re-edit.
  - **Atomic finalize:** an exception, `KeyboardInterrupt` or `SystemExit(1)` writes nothing.
  - **`record_all`:**
    - nested dicts flatten
    - `/` keys are sanitized, with one summary line
    - glob `fmt`/`desc` precedence over `[metrics]`, including `{0}`/`{-1}` desc templates
    - `row_key` DataFrames give cell keys plus a table
    - `stats=True` gives `Stat` values
    - tensor and array scalars unwrap; non-scalars are skipped and reported
    - a results-file path is registered as an input
  - **Change classes:**
    - a rendered change is `changed`
    - a sign flip, NaN, or a >10% move is `suspicious`
    - a move within rounding is `hidden`, is auto-acknowledged and logged
    - a new key is auto-acknowledged
    - `ack` moves the baseline, and a later change re-opens it
    - `on_change` receives valid JSON once
  - **Provenance CSV:** a golden file; reading order; every column filled for each kind.
  - **Tex emission:** round-trip of backslash-heavy strings (§18).
- **End-to-end (`examples/minimal/`):**
  1. `python train.py`, `vouch build`, `latexmk -pdf` (MiKTeX). There are no vouch warnings in the `.log`, and the values appear in the PDF text.
  2. **Tooltips:** compile with `\pdfcompresslevel=0`; the raw PDF contains the tooltip strings. A `final` build contains none.
  3. The provenance CSV has one row per cited key, with the correct `experiment`.
  4. Edit the model function: `check` exits 1 and names the unit.
  5. Re-run so the value moves: the build prints the change block with the citing sentence; `check --strict` fails with `changed`; the PDF tooltip says `CHANGED`; `ack` clears it.
  6. Flip a claim: `false-claim`.
  7. Hand-edit the run JSON: `store-edited`.
  8. Add `12.5\%`: `no-source` under `--strict`.
- **CLI:** golden outputs for `check`, `status`, `trace` and `changes`, with ASCII fallback. `--json` output validates against `schema/v1.json`.
- **LLM layer:**
  - `search` ranks the intended key first on a fixture of natural-language queries.
  - `suggest` maps `93.2\%` to the right key.
  - A pending key renders `[pending: …]` and fails `--strict`.
  - `vouch hook claude` stays under 200 ms on the example.
- **Agent dry run:** in a fresh copy of the example, a Claude subagent that has only the skill and the CLI is asked to "write a results paragraph comparing the models". Success means:
  - `check --strict` passes
  - there are no bare numbers
  - the comparison is a `\vouchclaim`
  - the difference comes from a `derive`

---

## 20. Open questions and risks

**Resolved in review (2026-09-18):**
- **CSV shape:** one CSV file for all cited keys (§10).
- **Rounding-only changes:** acknowledged automatically after being reported once (§9.2).
- **Sweeps and bulk registration:** one call registers a whole experiment: `record_all` (§4.3), `vouch run --values` and `vouch import` (§14).

**Still open:**

1. ~~Tooltip viewer support~~ **Resolved:** tooltips weren't portable (Chrome, Edge and PDF X show nothing), so `provenance=link` is the default: click-through links to a generated provenance appendix, which work in every viewer (§7.4).
2. **Implicit-run exit status.** `sys.exit(n≠0)` can't be seen from `atexit` (§4.1). Is documenting "use an explicit run" enough?
3. ~~`sys.monitoring` tool-id contention~~ **Resolved:** ids 3, 4, 5, 2 are tried in turn; with none free the run records module granularity and says why (§8.2).
4. **Coarse filesystem mtimes** (FAT, some network mounts) could make the stat-keyed hash cache miss a change. Hash when `mtime_ns` has 1-second granularity?
5. **v2 candidates:**
   - `vouch recompute KEY`: for a value recorded by `@vouch.track`, re-import the function at the recorded code state and call it with the recorded arguments (seeds included), comparing within a tolerance. Possible only when every argument was a plain value; calls that took arrays or models record only a description of them.
   - `vouch reproduce RUN`: re-execute the recorded command in a temporary checkout and compare values within tolerance
   - record signing
   - notebook tracking
   - Markdown/Typst renderers
   - a typed-and-checked mode for migrating asqc-style papers
