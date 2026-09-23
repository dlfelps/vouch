# Change notification

*Summarizes [SPEC.md §9](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

A number can be updated correctly and still leave its sentence wrong ("the best
of all models", "roughly doubles", "in all five seeds"). A change can also be
the first visible sign of a bug. So every change to a **cited** value is treated
as pending until someone has reviewed the sentences that cite it.

## The acknowledged baseline

`.vouch/acknowledged.json` (committed) stores, for each cited key, its raw and
rendered form, source run, hash, and when/by whom/why it was acknowledged.

- A key cited for the first time is acknowledged automatically by `vouch build`. You just wrote it, so there is nothing to review.
- `vouch check` is read-only. It reports differences from the baseline but never moves it.

## Change classes

`vouch build` and `vouch check` compare each cited key's current state with its
baseline:

| Class | Trigger | Default severity |
|---|---|---|
| `changed` | the rendered text differs from the acknowledged text | warning; **error with `--strict`** |
| `suspicious` | a `changed` value that also matches a problem heuristic (below) | warning labeled **POSSIBLE PROBLEM**; **error with `--strict`** |
| `hidden` | the raw value moved, but the printed rounding hides it | info; acknowledged automatically |
| `reformatted` | only the format changed (the raw value is identical) | info; acknowledged automatically |
| `figure-changed` | a cited figure's artifact hash differs from the acknowledged one | warning; **error with `--strict`** |
| `fragile` | a claim still holds, but by less than `changes.claim_margin` | warning |
| `false-claim` | a claim no longer holds | **error** (always) |

**Problem heuristics** (each can be toggled in `[changes]`): sign flip, non-finite,
large move (`\|new − old\| / \|old\| > rel_threshold`, default 10%), order of
magnitude, sample size change, provenance moved, type changed, direction
reversed.

## Where you hear about it

1. **`vouch build`** prints a block per change: old → new, the absolute and relative Δ, the heuristic that fired, and each citing sentence with its file:line:

   ```
   2 CHANGED VALUES — re-read the sentences below

     SUSPICIOUS  cifar.vit.acc   91.2\% → 72.4\%   (Δ −18.8 pts, −20.6%; large move)
       main.tex:118  "ViT-B trails ResNet-50 by only \vouch{cifar.resnet_vs_vit.pts} points…"
       claim cifar.resnet_beats_vit still holds (margin 28.7%)

   → fix any sentence that is now wrong, then: vouch ack <key>…  or  vouch review
   ```

   The most important changes come first: claims that stopped holding
   (`NOW FALSE`), then suspicious moves, then ordinary changes, then figures.

2. **`vouch check`** keeps listing pending changes until they are acknowledged. With `--strict` (CI, agents, optionally pre-commit) they block.
3. **The PDF** highlights pending values, and their tooltips say what they were.
4. **`vouch changes [--json | --md FILE]`** gives the full pending list. `--md` writes a shareable review report.
5. **The `on_change` hook.** If `changes.on_change` is set, `vouch build` runs that command once per newly detected batch and sends the changes as JSON on stdin.
6. **`vouch watch`** rebuilds on its own when new values are recorded, so the `vouch build` output above (and the `on_change` hook) arrives without you running anything. See [Comparing runs and live rebuilds](diff-and-watch.md#vouch-watch).

## Review and acknowledgment

```console
$ vouch review                        # interactive: one change at a time
  SUSPICIOUS cifar.vit.acc 91.2\% → 72.4\% …  (sentences shown)
  [a]ck  [s]kip  [o]pen main.tex:118  [d]etails  [q]uit > o      # opens $EDITOR / `code -g`
$ vouch ack cifar.vit.acc --why "bug fix in augmentation; text updated in §4.2"
$ vouch ack --all --why "re-ran all with 5 seeds"
$ vouch ack main                      # a table: every changed cell of it
$ vouch ack 'cifar.*'                 # a glob
```

Acknowledging moves the baseline and appends an event to `.vouch/history.jsonl`
recording the key, old → new, who, when and why — a permanent changelog of the
paper's numbers. `vouch ack` and `vouch accept` rebuild the generated files
themselves, so highlights and tooltips update without a separate `vouch build`.

Uncited values, and questions like "what did this re-run change compared with
the last commit?", are the job of [`vouch diff`](diff-and-watch.md#vouch-diff).
It compares every recorded value with a git revision, shows each move's size
and direction (better or worse), and never touches the baseline.

Acknowledgment is a human action; agents surface changes and fix text, but
don't acknowledge without approval — see [Claude Code](../llm/claude-code.md).
