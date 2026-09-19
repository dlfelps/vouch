# Derived values, claims and tables

*Summarizes [SPEC.md §5](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

Many numbers in a paper are computed *from* results: a difference, a ratio, a
best-of, a mean over datasets, or a table assembled from several runs. These
live in `vouch_values.py` (configurable). `vouch build` evaluates them. They are
never computed in anyone's head, human or LLM.

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

`vouch.claim` and `vouch.table` are the same functions experiments use to record
claims and tables. Called with a value (`vouch.claim(key, holds)`), they record
it in the active run. Called with only a key and options, they are decorators
for `vouch_values.py`.

The values modules are inert outside `vouch build`: importing one from a
notebook or a test registers nothing and runs no definition. Inside a build they
must not record: `vouch.record()` (or a `savefig`) there is an error, because a
value computed from other values belongs in a `@vouch.derive`.

## The accessor `v`

- `v[key]` returns the raw value: `Stat` for a stat, `float` for a float, `bool`
  for a claim, and a list of row dicts for a table. Subfield keys such as
  `.mean` and aliases work.
- `v.keys("cifar.*.acc")` lists the recorded keys matching a glob, for best-of
  and mean-over-datasets definitions. The list itself is a dependency.
- Every access is logged, with a content hash of the value read. The logged
  keys become the derivation's **dependencies**, and through them the runs it
  rests on.
- Accessing a pending (expected) key raises `vouch.Pending`. The derivation
  becomes pending in turn, and so does everything citing it.
- Accessing an unknown key fails the derivation with a did-you-mean suggestion.
- Derivations may use other derived keys. Evaluation is lazy and memoized, with
  cycle detection (`derive-cycle`).
- A definition that raises is reported as `derive-error` with the exception and
  the file:line where it happened; the other definitions are still evaluated.

## Artifact inputs

`inputs=[...]` lists paths the function receives as positional arguments after
`v`. Loaders are chosen by extension:

| Extension | Loaded as |
|---|---|
| `.csv` | `list[dict[str, str]]` (or a DataFrame with `as_frame=True`) |
| `.json` | the parsed JSON |
| `.jsonl` | a list of parsed records |
| anything else | `pathlib.Path` |

Each input must be an artifact of some run, or be listed under `[inputs] external`
in `vouch.toml`. A derivation that reads a file nobody produced is a provenance
hole (`untracked-input` error).

## Claim helpers

A claim function returns a `bool`, or a `Verdict` produced by these helpers:

| Helper | Holds when |
|---|---|
| `gt(a, b)`, `ge`, `lt`, `le` | the comparison holds |
| `between(x, lo, hi)` | `lo ≤ x ≤ hi` |
| `approx(x, y, rel=0.05)` | `\|x − y\| ≤ rel·\|y\|` |
| `all_of(...)`, `any_of(...)` | combinations of the above |

A `Verdict` carries `holds`, a human-readable `explanation` (`0.932 > 0.912`) and
a relative **margin**: how far the claim is from flipping. A `Stat` is compared
by its mean, and a `Verdict` is truthy, so `if vouch.gt(a, b):` works.

A claim that holds by less than `changes.claim_margin` (default 1%) is reported
as `fragile`.

## Tables

A table is recorded data, `run.table(...)`, or derived data, `@vouch.table(...)`.
It is rendered at **build** time, so its presentation can be changed without
re-running anything.

Rendering produces only the **body rows** (`a & b & c \\`). You write the
`tabular`/`booktabs` environment and the header, so styling stays yours.

- `highlight={"col": "max" | "min"}` bolds the best cell per column. `second="underline"` marks the runner-up.
- `Stat` cells render `mean \pm std`.
- `midrules=[2, 5]` inserts `\midrule` after the given body rows.
- Each cell is citable as `\vouch{<table>.<row>.<col>}` and has a tooltip.
- `[tables.<key>]` in `vouch.toml` overrides `fmt`, `highlight`, `second` and `midrules` for a recorded or derived table, so presentation changes need neither a re-run nor a code edit.

## Expected values (placeholders)

`vouch.expect(key, desc=…, producer=…)` declares a number the paper needs before
any run produces it:

- In the PDF it renders as a boxed **[pending: key]**, visibly different from an unknown key's **??key**.
- `vouch todo` lists pending keys with their `producer` commands.
- The check reports it as `pending`: a warning in drafts, an error under `--strict` and in `final` builds.
- Once a run records the key, the placeholder resolves automatically.

## Evaluation and persistence

`vouch build` imports each values module and evaluates every definition. It
writes `.vouch/derived.json` (committed, deterministic), which holds the
semantic hash of each values module, each definition's results, its
dependencies with content hashes, the runs those values came from, and any
evaluation problems (`derive-error`, `derive-cycle`, `untracked-input`, …).

**Code is hashed per module, not per definition.** A module-level change (a
constant, a helper) can affect any of them, so the whole set is re-evaluated
when any of its code changes semantically.

**When anything moved, rebuild; otherwise don't even import.** A build first
compares `derived.json` with the present: the code hashes, every dependency's
current value hash, every input's file hash. If nothing moved, it uses the
stored results without importing anything.

`vouch check` does **not** evaluate definitions: checking never imports pandas
or runs user code. It loads `derived.json` and reports one `out-of-sync` error
listing what moved, with the fix `vouch build`.

## Aliases

`vouch.alias(short, full)` in a values module lets the paper cite `full`, and
every key under it, by a shorter name:

```python
vouch.alias("resnet", "evaluate.cifar.resnet.lr_0_001")     # a long @vouch.track key
```

```latex
ResNet reaches \vouch{resnet.acc} (\vouch[.3f]{resnet.acc.mean}).
```

See also: [Recording (Python API)](recording.md), [LaTeX interface](latex.md).
