# Check & review: check, changes, review, ack, accept

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
