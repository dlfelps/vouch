# Comparing runs and live rebuilds

*Summarizes [SPEC.md §9.5 and §15](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

Two commands help between "I re-ran the experiment" and "the paper is right
again":

- **`vouch diff`** says what a re-run did to every recorded value, compared
  with a git revision.
- **`vouch watch`** rebuilds the generated LaTeX whenever new values are
  recorded or you save a source file, and prints what moved each time.

Neither one runs an experiment or acknowledges a change. They read what the
runs recorded and report on it.

## `vouch diff`, `vouch changes` or `vouch check`?

All three report values that moved, but each compares against something
different:

| | Compares | Covers | Moves anything? |
|---|---|---|---|
| [`vouch check`](checks-and-lints.md) / [`vouch changes`](change-notification.md) | the **acknowledged baseline**: what someone last re-read | **cited** values only | no; `vouch ack` moves the baseline |
| `vouch diff` | a **git revision** (default `HEAD`) | **every** recorded value, param, claim, table cell and figure | no, never |
| `vouch watch` | the **previous build** in this session | every value, as it changes | writes the generated files, like `vouch build` |

Use `vouch changes` to answer "which sentences do I need to re-read?". Use
`vouch diff` for "what did this re-run do?", including the values the paper
doesn't cite: timings, training metrics, sweep points you left out of the
table. Those often explain a cited value's move.

## `vouch diff`

### Choosing the two sides

```console
$ vouch diff                 # HEAD vs the working tree
$ vouch diff main            # the tip of main vs the working tree
$ vouch diff v1.0 HEAD       # two revisions; the working tree is ignored
$ vouch diff HEAD~1          # what the last commit plus uncommitted work changed
```

A revision is anything git accepts as a commit: a branch, a tag, a hash,
`HEAD~3`, and so on. vouch reads that revision's run records
(`.vouch/runs/*.json`) and `.vouch/derived.json` with `git ls-tree` and
`git cat-file`. Nothing is checked out, and your working tree is never touched.

The **working tree** side is the run records on disk now plus the derived
values from the last `vouch build`. If `vouch_values.py` or the records changed
since that build, a note says the derived values are out of date. Run
`vouch build` (or leave `vouch watch` running) and diff again.

Because a revision's side is read from git, **commit your results** for them to
count: `.vouch/runs/` and `.vouch/derived.json` (everything under `.vouch/`
except `cache/`, as [Workflow integration](workflow-integration.md#git)
recommends). If a revision has no `derived.json`, every derived value, claim
and table cell shows up as `added`.

### What is compared

| Kind | Compared by |
|---|---|
| recorded values and params | the raw value, with the same float tolerance `vouch build` uses |
| derived values | the value in `derived.json` |
| claims | whether it holds, plus its margin |
| table cells | each cell, as `TABLE.ROW.COLUMN` |
| figures | the artifact's content hash |
| mean ± std subfields, tuple elements | only with `--all` |

Aliases are skipped, since they repeat their target. Formats, units and
`better` come from the **current** `vouch.toml` for both sides, so a key is
rendered and judged the same way on each. Citation counts (the `●` mark and
`--cited`) come from the current paper.

### Reading the output

```console
$ vouch diff
vouch diff: HEAD → working tree · 41 changed · 0 added · 1 removed · 29 unchanged

  run experiment
      ● evaluate.knn.n_train_640.acc         87.3 +/- 1.4% → 78.4 +/- 1.3%   ↓ Δ -8.88 pts, -10.2% relative   worse   ! large move (10%)
      ● experiment.param.k                   15 → 1   ↓ Δ -14, -93.3%   ! order-of-magnitude change; large move (93%)
        evaluate.knn.n_train_20.train_acc    76.0 +/- 5.5% → 100.0 +/- 0.0%   ↑ Δ +24 pts, +31.6% relative   better   ! large move (32%)
        ...
  derived
      ● gap                                  5.8 → -3.0   ↓ Δ -8.88, -152.1%   worse   ! sign flip; large move (152%)
  claims
      ● knn_wins_large                       HOLDS → FALSE   worse   ! NOW FALSE; margin 0.0717 → -0.0373
  table learning_curve
      ● learning_curve.640.knn               87.3 +/- 1.4% → 78.4 +/- 1.3%   ↓ Δ -8.88 pts, -10.2% relative   ! large move (10%)
        ...
  removed
    - ● crossover                            80
  figures
    ~ ● paper/figures/learning_curve.pdf     the figure file changed (run experiment)

  ● cited in the paper; `vouch changes` lists the sentences to re-read
```

The header counts `changed`, `added`, `removed` and `unchanged` keys, after any
filters. Each line has:

| Part | Meaning |
|---|---|
| `+` / `-` / `~` | added / removed / a figure whose file changed. A changed value has no mark. |
| `●` | the paper cites this key (or the table it belongs to) |
| `old → new` | both sides as the paper would print them |
| `↑` / `↓` | direction of the move |
| `Δ` | absolute and relative change. For a percentage, in percentage points (`pts`), as printed. For a mean ± std, the change in the mean. |
| `better` / `worse` | only for keys with `better = "higher"` or `"lower"` (from `record(..., better=)` or `[metrics]`). A claim that stops holding is `worse`, and one that starts holding is `better`. |
| `! ...` | problem heuristics, the same ones `vouch build` uses: sign flip, large move (`changes.rel_threshold`), order of magnitude, sample size changed, moved to another run, type changed. For claims: `NOW FALSE`, `now holds`, and the margin. |

`better` and `worse` follow the declared direction and nothing else. In the
example, k-NN's training accuracy reaching 100% is labeled `better`, although
it means the model memorised noisy labels. Read the verdict as "moved in the
direction you said you want", not as a judgment.

**Order.** Groups come in this order: one per run, then `derived`, `claims`,
one per table, `removed`, `figures`. Within a group, cited keys come first,
then `worse` moves, then the rest.

### Narrowing it down

```console
$ vouch diff --cited                     # only keys the paper cites
$ vouch diff --key train_acc             # keys containing "train_acc"
$ vouch diff --key '*.acc' --key gap     # a glob, and a substring; either matches
$ vouch diff --all                       # also .mean, .std, ... and tuple elements
```

`--key` is repeatable. A key is kept if it contains any pattern as a substring
or matches it as a glob (case-sensitive). For a figure, the pattern is matched
against its path. Timings and other noisy values change on every run, so
excluding them with a pattern for the metrics you care about is often the
quickest way to read a diff.

### Reports and machine-readable output

`--md FILE` writes a Markdown report: a heading with both sides, the counts,
and one table per group, with cited keys in bold. It's meant for a pull
request description or a message to co-authors:

```console
$ vouch diff main --md diff.md
wrote diff.md (42 difference(s))
```

`--json` prints the `vouch/1` envelope with `from`, `to`, `unchanged`,
`counts` (`changed`, `added`, `removed`), `notes` and `items`. Each item has:

```json
{
  "key": "evaluate.knn.n_train_640.acc",
  "class": "changed",
  "kind": "value",
  "group": "run experiment",
  "old": "87.3 +/- 1.4%",
  "new": "78.4 +/- 1.3%",
  "old_raw": {"mean": 0.873, "std": 0.01414, "n": 5, "min": 0.858, "max": 0.891},
  "new_raw": {"mean": 0.7842, "std": 0.0132, "n": 5, "min": 0.766, "max": 0.8},
  "delta": -0.0888,
  "relative": -0.1017,
  "direction": "down",
  "verdict": "worse",
  "reasons": ["large move (10%)"],
  "cited": 1
}
```

`class` is `added`, `removed` or `changed`. `kind` is `value`, `param`,
`claim`, `table-cell`, `stat-field`, `element` or `figure`. `delta` is in the
raw unit (0.0888 is 8.88 percentage points). `cited` is the number of
citations.

Agents get the same data from the MCP tool
[`diff_values(rev, to, cited)`](../llm/mcp-server.md).

### Exit codes

`0` whether or not anything differs, since a diff is information, not a gate.
`2` when vouch could not compare: not a git repository, an unknown revision,
or a store outside the repository. Use [`vouch check`](checks-and-lints.md)
when you need a pass/fail.

## `vouch watch`

```console
$ vouch watch [--interval SEC] [--then CMD] [--no-notify] [--quiet]
```

`vouch watch` runs `vouch build` once, then again whenever something the build
reads changes. Leave it running in a terminal while you run experiments and
write.

### What triggers a rebuild

| Watched | Why |
|---|---|
| `.vouch/runs/*.json` | an experiment finished, or `vouch run` / `vouch import` recorded values |
| `vouch.toml` | formats, metrics, paper settings |
| the values modules (`[python] values_modules`, usually `vouch_values.py`) | derived values, claims, tables |
| every `.tex` file of every paper | new or changed citations |

The `.tex` list comes from the last successful build, so a file you `\input`
later is picked up after the next rebuild. The files a build writes (the values
file, tables, provenance CSV, `.vouch/derived.json`, the catalog) are never
watched, so a build can't trigger itself.

Files are compared by modification time and size every `--interval` seconds
(default 1). This uses only the standard library and behaves the same on every
OS and on network filesystems. When a change is seen, the watcher waits until a
full interval passes with no further changes, then builds once. A sweep that
writes several records, or an editor that saves in several steps, causes one
rebuild rather than many.

### What it prints

Each rebuild starts with a timestamp and the files that changed (up to four,
then `+N more`). Then:

- **The values that moved** since the previous build, in the same format as
  [`vouch diff`](#reading-the-output), including uncited ones. The first build
  after starting has nothing to compare with, so it prints none.
- **The build report**, the same as `vouch build` prints: what it wrote, and the
  `CHANGED VALUES` block for cited values. With `--quiet`, just errors and
  one line: `wrote N file(s)` or `unchanged`, plus the number of pending changes.
- **The `--then` result**, if any: `✓ CMD`, or `✗ CMD (exit N)`.

```console
$ vouch watch --quiet --then "latexmk -pdf -cd paper/main.tex"
[18:28:19] vouch watch: building, then watching for new values (Ctrl-C to stop)
  wrote 3 file(s)
  ✓ latexmk -pdf -cd paper/main.tex

[18:29:02] .vouch/runs/experiment.json changed
  42 value(s) moved since the last build:
    run experiment
        ● evaluate.knn.n_train_20.acc          78.7 +/- 2.5% → 59.0 +/- 10.0%   ↓ Δ -19.6 pts, -24.9% relative   worse   ! large move (25%)
        ...
    claims
        ● knn_wins_large                       FALSE → HOLDS   better   ! now holds; margin -0.0373 → 0.0717
  wrote 3 file(s)
  ✓ latexmk -pdf -cd paper/main.tex

[18:31:40] paper/main.tex changed
  unchanged
```

### Recompiling the PDF: `--then`

`--then CMD` runs `CMD` through the shell, from the project root, after each
build that **wrote files**. Saving the paper without changing a citation writes
nothing, so it doesn't recompile. Anything the command writes is ignored by the
watcher. A failing command is reported and the watcher keeps going.

```console
$ vouch watch --then "latexmk -pdf -cd paper/main.tex"
$ vouch watch --then "make paper"
```

If you already run `latexmk -pvc` or an editor that recompiles on save, you
don't need `--then`: those pick up the files `vouch watch` writes.

### When a build fails

A half-written `vouch_values.py`, a typo in `vouch.toml` or a malformed record
doesn't stop the watcher. It prints `✗ build failed: ...` and
`(still watching; fix it and save)`, then waits for the next change. The values
that moved are then reported against the last build that succeeded.

### The `on_change` hook

Each rebuild is a normal `vouch build`, so if `changes.on_change` is set it
runs once per newly detected batch of changes to cited values (see
[Change notification](change-notification.md#where-you-hear-about-it)).
`--no-notify` turns that off for the session.

### What it doesn't do

- **Run experiments.** It re-renders what was recorded. Run experiments
  yourself, on a cluster, or from another tool. When a record lands in
  `.vouch/runs/` the watcher picks it up.
- **Acknowledge changes.** Pending changes stay pending until you
  `vouch ack` or `vouch review`, as with `vouch build`.
- **Check.** It doesn't run `vouch check`. Run that before you commit (or let
  the [pre-commit hook](workflow-integration.md#pre-commit) do it).

Ctrl-C stops it (exit code 0).

## A typical loop

```console
# terminal 1
$ vouch watch --then "latexmk -pdf -cd paper/main.tex"

# terminal 2
$ python experiment.py            # watch rebuilds the paper and prints what moved
$ vouch diff --key acc            # everything this re-run changed since the last commit
$ vouch review                    # re-read the cited ones and acknowledge
$ vouch check --strict
$ git add -A && git commit -m "re-run with the fixed augmentation"
```

On a branch, `vouch diff main --md diff.md` puts the whole branch's effect on
the results into the pull request.

See also: [`vouch diff`](../cli/check.md#vouch-diff) and
[`vouch watch`](../cli/setup.md#vouch-watch) in the CLI reference,
[Change notification](change-notification.md),
[Workflow integration](workflow-integration.md).
