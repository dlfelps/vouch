# Recording (Python API)

*Summarizes [SPEC.md §4](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

There are three ways to record, and they combine freely in one run:

| Way | Code change | Use it when |
|---|---|---|
| `@vouch.track` on the function that computes the numbers | one decorator | **the default.** Each value records the call that produced it (function, every argument, call site), which is the strongest provenance vouch has |
| `[[track]]` in `vouch.toml` + `python -m vouch.exec` | none | the code can't or shouldn't import vouch: someone else's script, a frozen baseline, a quick look before committing to vouch |
| `vouch.record` / `vouch.record_all` | one line per value or per dict | a number that isn't a function's return value: an aggregate computed inline, a value read from a results file, a param |

## Zero-configuration use: an implicit run

```python
import vouch

vouch.params(vars(args))                         # optional: parameters become citable values
acc = vouch.record("cifar.resnet.acc", evaluate(model), fmt=".1pct",
                   desc="top-1 test accuracy on CIFAR-10", better="higher")
```

- The first vouch call outside a `with vouch.run(...)` block starts an **implicit run**. Its id is the entry script's project-relative path with `/` replaced by `.` and without the `.py` suffix, e.g. `experiments.train`.
- The implicit run is finalized at interpreter exit, unless an uncaught exception occurred. A script that ends with `sys.exit(n)`, n ≠ 0, is not detectable from `atexit`. Use an explicit run when the exit code matters.
- `record()` returns the value unchanged, so it can be written inline.

## Explicit runs

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
- **Parameters.** `params=` accepts a dict, an `argparse.Namespace`, a dataclass, or any object with `to_dict()`/`to_container()` (OmegaConf, Pydantic). Each is citable as `<run>.param.<name>`.
- **Command.** `sys.argv` and the interpreter are recorded, which is what gives `vouch status` its exact re-run command.

## Recording everything at once: `record_all`

```python
metrics = evaluate(model, test_set)            # {"acc": 0.932, "loss": 0.211, "f1": {"macro": 0.90, "micro": 0.93}}
vouch.record_all(metrics, prefix=f"{dataset}.{model_name}")
# → cifar.resnet.acc, cifar.resnet.loss, cifar.resnet.f1.macro, cifar.resnet.f1.micro
```

Metadata comes from project-wide metric conventions defined once in `vouch.toml`:

```toml
[metrics]
"*.acc"    = { fmt = ".1pct", better = "higher", desc = "top-1 test accuracy, {1} on {0}" }
"*.loss"   = { fmt = ".3f",   better = "lower",  desc = "test cross-entropy, {1} on {0}" }
"*.f1.*"   = { fmt = ".3f",   better = "higher", desc = "{-1} F1, {1} on {0}" }
"*.time_s" = { fmt = ".0f",   unit = "s",        better = "lower", desc = "wall-clock training time, {1} on {0}" }
```

In a `desc` template, `{0}`, `{1}`, … are key segments and `{-1}` is the last one.

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
| `table` | also register the DataFrame or list of rows as a table under this key, so one call gives both cell keys and a `\vouchtable` |

**Rules:**

- **Metadata precedence:** an exact-key argument, then the longest matching glob argument, then `[metrics]` in `vouch.toml`, then the type default.
- **Key sanitizing.** `/` becomes `.` (so `eval/accuracy` → `eval.accuracy`). Other invalid characters become `_`. All renames are reported in one summary line.
- **Scalars are unwrapped.** Single-element numpy arrays and torch tensors become scalars via `.item()`.
- **Missing descriptions** after all defaults are applied produce one aggregated warning.
- `record_all` returns its input unchanged, so it can wrap an expression.

Sweeps and multi-seed experiments reduce to one call:

```python
# a learning-rate sweep → sweep.<lr>.<metric> keys plus a citable table
vouch.record_all(sweep_df, prefix="sweep", row_key="lr", table="lr_sweep")

# per-seed lists → Stat values (mean ± std, n)
vouch.record_all({"acc": accs_per_seed, "loss": losses_per_seed}, prefix="cifar.resnet", stats=True)
```

The same single-step registration is available outside Python: `vouch run`
ingests a program's whole `$VOUCH_VALUES` JSON, and `vouch import` registers an
existing results file — see [Non-Python experiments](non-python-experiments.md).

## Recording a function's results: `@vouch.track`

Decorating an experiment function records what it returns on every call, keyed
by the call's arguments:

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

**Keys** are the function's name followed by every argument, defaults included,
in signature order:

- A string argument is written as its value (`cifar`).
- A number, bool or `None` is written as `name_value` (`lr_0_001`, `seed_3`, `flag_true`).
- An enum is written as its member name; a short list of simple values as `layers_64-64`.
- Arguments that can't be written into a key (arrays, models, dicts, `self`) are left out of it and described in the call metadata instead.
- Methods use the class-qualified name (`Trainer.fit…`).
- Keys longer than 100 characters are shortened with a hash suffix, so they stay unique.
- `key="{dataset}.{model}"` replaces the scheme with a template, checked against the signature when the function is decorated.

**Results:**

- a number or `Stat` → the base key
- a tuple → one key per element (`.0`, `.1`, … by position, or named with `returns=("mean", "std")`)
- a dict or dataclass → one key per (nested) field
- a DataFrame or list of rows → one key per cell
- a list of numbers → one value (a series); its elements are citable as `key.0`, `key.1`, …
- `None` → nothing (warned once)

```python
@vouch.track(returns=("mean", "std"), desc="bootstrap accuracy ({-1})")
def bootstrap(model: str, n: int = 1000):
    ...
    return mean, std
# -> bootstrap.vit.n_1000.mean  "bootstrap accuracy (mean)"
#    bootstrap.vit.n_1000.std   "bootstrap accuracy (std)"
```

`fmt`, `desc`, `unit`, `better`, `include` and `exclude` work as in `record_all`,
and so do `[metrics]` defaults.

**`over=`** names arguments whose calls are combined when the run ends. Numbers
become a `Stat` (mean, std, n); a value that is the same on every call is kept
as is; anything else is skipped with a note.

**Every value records its call** — function, args, `over` list, each seed's
result and duration, and call sites — which is what makes `vouch trace` and
`vouch explore` show the full per-seed breakdown.

**Timing is a value too, by default.** Every tracked call also records its
duration as `<key>.time`, in seconds. `time="walltime"` names it differently;
`time=False` leaves the value out.

**Bookkeeping never breaks the experiment:**

- The result passes through unchanged.
- An exception inside the function records nothing and propagates.
- A recording problem is a warning, and an error only with `VOUCH_STRICT=1`.
- Async functions are supported.

## Tracking without touching the code

The functions to track can be listed in `vouch.toml` instead of decorated. The
effect is the same as `@vouch.track` with the same options:

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

A script that doesn't import vouch runs under `python -m vouch.exec script.py args…`,
which starts tracking before the script's first line. Only first-party functions
can be listed, not library code; module bodies, lambdas and comprehensions never
match.

## API reference

| Call | Purpose |
|---|---|
| `vouch.run(id, params=None, prefix=None)` | Context manager for an explicit run. `prefix` is prepended to every key recorded in the run. |
| `vouch.record(key, value, *, fmt=None, unit=None, desc=None, better=None)` → value | Record a value. `better ∈ {"higher", "lower", None}`. |
| `vouch.record_all(values, *, prefix=None, fmt=None, desc=None, …)` → values | Record every value in a dict, DataFrame or results file in one call. |
| `vouch.claim(key, holds, *, desc=None, values=None)` → holds | Record a boolean claim. |
| `vouch.input(path, *, mode=None)` | Declare an input file or directory. |
| `vouch.artifact(path, *, kind=None)` | Declare an output file. `kind ∈ {"figure", "data", "model", "file"}`. |
| `vouch.table(key, data, *, columns=None, row_key=None, fmt=None, highlight=None, desc=None)` | Record a table. |
| `vouch.params(obj)` | Set the implicit run's parameters. |
| `vouch.expect(key, *, desc, producer=None, fmt=None, unit=None, better=None)` | Declare a placeholder. |
| `vouch.Stat.of(samples)` / `vouch.Stat(mean, std, n, *, min=None, max=None)` | A summary statistic. `std` is the sample std (ddof = 1). `ci95` uses Student-t quantiles. |

Module-level functions act on the active run: the explicit one if inside a `with`,
otherwise the implicit one. `run.<method>` is the same function bound to a
specific run.

## Value types

| Python value | Stored `type` | Default rendering |
|---|---|---|
| `bool` | `bool` | `true`/`false` (usually cited through claims) |
| `int` (incl. numpy ints) | `int` | `d` |
| `float` (incl. numpy floats) | `float` | `format.default_float` (`.3g`) |
| `str` | `str` | LaTeX-escaped text |
| `tuple`/`list` of numbers | `tuple` | `a--b` for 2 elements (a range), comma-joined otherwise |
| `Stat` | `stat` | `mean \pm std` |

**Citing part of a value.** A `Stat` exposes `key.mean`, `key.std`, `key.n`,
`key.min`, `key.max` and `key.ci95`. A `tuple` value (up to 64 elements) exposes
each element by position: `vouch.record("ci", (0.91, 0.95))` makes `\vouch{ci.0}`
and `\vouch{ci.1}` citable.

??? note "Run record format (`.vouch/runs/<id>.json`)"
    The file is deterministic: keys are sorted, and every map is written one
    entry per line, so a changed value is a one-line diff.

    ```json
    {
      "schema": "vouch/1",
      "run": "cifar_resnet",
      "status": "complete",
      "entry": "experiments/train.py",
      "command": ["python", "experiments/train.py", "--model", "resnet50", "--seeds", "5"],
      "params": {"model": "resnet50", "optim.lr": 0.1, "seeds": 5},
      "started": "2026-09-12T14:03:11Z",
      "duration_s": 3412.5,
      "git": {"commit": "0fdc530e7c1b9f6a2d8e4b3c5a7f9e1d2c4b6a8f", "dirty": false},
      "env": {"python": "3.13.5", "platform": "win-amd64", "packages": {"numpy": "2.1.0", "torch": "2.5.1"}},
      "code": {
        "granularity": "function",
        "units": {"experiments/train.py::main": "41d0e7c2aa9b1f05"},
        "files": {"experiments/train.py": "e1b2c3d4e5f6a7b8"}
      },
      "inputs": {"data/cifar10.npz": "sha256:5f1c…"},
      "values": {
        "cifar.resnet.acc": {"type": "stat", "value": {"mean": 0.93214, "std": 0.0041, "n": 5},
                             "fmt": ".1pct", "desc": "top-1 test accuracy on CIFAR-10", "better": "higher",
                             "site": "experiments/train.py:88"}
      },
      "claims": {"cifar.resnet.no_divergence": {"holds": true, "desc": "no seed diverged"}},
      "artifacts": {"paper/figs/curve.pdf": {"hash": "sha256:a41e…", "kind": "figure"}},
      "record_hash": "sha256:3c9e…"
    }
    ```

    `code.units` holds semantic hashes, truncated to 16 hex characters.
    `record_hash` is SHA-256 over the canonical JSON of the record without that
    field; `vouch check` recomputes it and reports `store-edited` on a mismatch.

See also: [Derived values, claims, tables](derived-values.md), [Freshness](freshness.md).
