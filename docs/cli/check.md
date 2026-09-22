# Check & review: check, changes, diff, review, ack, accept

## `vouch check`

The gate: cited values exist, are fresh, and are acknowledged. Read-only, never
runs your code, targets under a second.

```console
$ vouch check [--strict] [--quiet] [--verbose] [--json] [--no-env] [--paper FILE] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `--strict` | treat warnings as errors (CI, agents) |
| `--quiet` | print only problems |
| `--verbose` | also print informational notes |
| `--json` | the `vouch/1` JSON envelope |
| `--no-env` | skip the package-version drift check |

Exit codes: `0` pass, `1` fail, `2` could not check.

```console
$ vouch check
vouch check: paper/main.tex · 16 citations · 1/1 cited runs fresh
✓ OK
```

Full check list: [Checks and lints](../guide/checks-and-lints.md).

## `vouch changes`

Cited values that changed since last acknowledged, with the citing sentences.

```console
$ vouch changes [--json] [--md FILE] [--root DIR]
```

`--md FILE` writes a shareable Markdown review report, e.g. for co-authors.

## `vouch diff`

What moved between two states of the store: every recorded value, parameter,
claim, table cell and figure, not only the cited ones. The default is the last
commit (`HEAD`) against the values on disk now. Run it before committing a
re-run's records, or to write up a PR.

```console
$ vouch diff [REV [REV2]] [--key PATTERN]... [--cited] [--all] [--json] [--md FILE] [--root DIR]
```

| Form / flag | Meaning |
|---|---|
| `vouch diff` | `HEAD` vs the working tree |
| `vouch diff REV` | `REV` (a branch, tag, commit, `HEAD~3`, ...) vs the working tree |
| `vouch diff REV REV2` | two revisions |
| `--key PATTERN` | only keys containing this substring or matching this glob (repeatable) |
| `--cited` | only keys the paper cites |
| `--all` | also each mean ± std's subfields (`.mean`, `.std`, ...) and tuple elements |
| `--json` | the `vouch/1` envelope: `from`, `to`, `counts`, `unchanged`, `items`, `notes` |
| `--md FILE` | a Markdown report, one table per group, e.g. for a PR description |

```console
$ vouch diff
vouch diff: HEAD → working tree · 5 changed · 1 added · 0 removed · 12 unchanged

  run train
      ● toy.centroid.acc      80.2 ± 3.5% → 79.3 ± 3.1%   ↓ Δ -0.867 pts, -1.1% relative   worse   ! sample size changed (n=5 -> 3)
      ● toy.majority.acc      49.9 ± 4.5% → 50.0 ± 3.5%   ↑ Δ +0.1 pts, +0.2% relative   better   ! sample size changed (n=5 -> 3)
      ● toy.centroid.n_test   200 → 50   ↓ Δ -150, -75.0%   ! large move (75%)
      ● train.param.seeds     5 → 3   ↓ Δ -2, -40.0%   ! large move (40%)
        toy.majority.n_test   200 → 50   ↓ Δ -150, -75.0%   ! large move (75%)
    +   toy.centroid.f1       0.81

  ● cited in the paper; `vouch changes` lists the sentences to re-read
```

How to read a line:

- `+` added, `-` removed, `~` a figure file that changed. `●` means the key is cited.
- **Δ** is the absolute and relative change. For percentages it is in
  percentage points, as the paper prints them. For a mean ± std it is the change in the mean.
- **↑ / ↓** is the direction. **better / worse** appears when the key has
  `better = "higher"` or `"lower"` (from `record(..., better=)` or `[metrics]`).
  A claim that stops holding is `worse`.
- **!** lists the same problem heuristics `vouch build` uses: sign flip, large
  move (`changes.rel_threshold`), order of magnitude, sample size changed,
  moved to another run, type changed.

Groups come in this order: each run, derived values, claims, tables, removed
keys, figures. Within a group, cited keys come first and `worse` before the rest.

Revisions are read with git (`git ls-tree`, `git cat-file`), and nothing is
checked out. Formats, units and `better` come from the *current* `vouch.toml`
on both sides, so both sides are rendered and judged the same way. The
working-tree side uses the last `vouch build`'s derived values. If they are out of
date, a note says so. Exit codes: `0` (with or without differences), `2` when
it could not compare (not a git repository, unknown revision).

!!! note "`vouch diff` or `vouch changes`?"
    `vouch changes` covers **cited** values against what someone last
    **acknowledged**, and waits until the sentences are re-read. `vouch diff`
    covers **every** recorded value against a **git revision** and answers "what
    did this re-run do?". It never acknowledges anything.

## `vouch review`

Step through pending changes interactively.

```console
$ vouch review
  SUSPICIOUS cifar.vit.acc 91.2\% → 72.4\% …  (sentences shown)
  [a]ck  [s]kip  [o]pen main.tex:118  [d]etails  [q]uit >
```

`o` opens `$EDITOR` / `code -g` at the citing line.

## `vouch ack`

Acknowledge changed values after re-reading their sentences.

```console
$ vouch ack KEY... [--all] [--run RUN] [--why TEXT] [--root DIR]
```

| Flag | Meaning |
|---|---|
| `KEY...` | keys, globs (`'cifar.*'`), or a table name — every changed cell of it |
| `--all` | every pending change |
| `--run RUN` | every pending change from this run |
| `--why TEXT` | recorded with the acknowledgment (default `"acknowledged"`) |

One of `KEY...`, `--all` or `--run` is required. `vouch ack` and `vouch accept`
rebuild the generated files themselves, so highlights and tooltips update
without a separate `vouch build`. Acknowledging is a human action — see
[Change notification](../guide/change-notification.md).

## `vouch accept`

Record that a stale run's result still stands.

```console
$ vouch accept RUN --why TEXT
```

`--why` is required and is recorded with who accepted it and when, in
`.vouch/accepted.toml`. See [Freshness](../guide/freshness.md#accepting-a-reviewed-staleness).
