# vouch

Every number in your paper, vouched for by the code that produced it.

Experiments **record** their results as they run. The paper **cites** them by key,
`\vouch{key}`, and never contains a typed number. `vouch check` proves that every
cited value exists, comes from a run whose code hasn't changed since, and that
every claim still holds. When a cited value changes, vouch shows you the sentences
to re-read. Any number traces back to the function, arguments, seeds, command and
commit that produced it, from the terminal, a local web page, or by clicking it in
the PDF.

It is built for people and for LLM agents writing papers: the correct number is
always cheaper to use than a guessed one.

```console
$ pip install -e .          # from a clone; Python ≥ 3.10, no dependencies
$ vouch init                # finds your paper, writes vouch.toml, copies vouch.sty
```

## 1. Record

Decorate the function that computes your results. Every call is recorded, keyed by
the function and its arguments, together with the call itself: arguments, seeds,
each seed's result, how long it took, and the exact code that ran.

```python
import vouch

@vouch.track(over="seed")                 # calls that differ only in seed -> mean ± std
def evaluate(dataset: str, model: str, seed: int = 0) -> dict:
    ...
    return {"acc": acc, "loss": loss}

for model in ("resnet", "vit"):
    for seed in range(5):
        evaluate("cifar", model, seed=seed)
# -> evaluate.cifar.resnet.acc, evaluate.cifar.resnet.loss, evaluate.cifar.resnet.time, ...
```

Run the script as usual: `python train.py`. Formats and descriptions for whole
families of keys go in `vouch.toml`:

```toml
[metrics]
"*.acc"  = { fmt = ".1pct", better = "higher", desc = "top-1 test accuracy" }
"*.loss" = { fmt = ".3f",   better = "lower",  desc = "test loss" }
```

Other ways to record, when a decorator doesn't fit:

| | |
|---|---|
| No code changes at all | list the function in `vouch.toml` (`[[track]] function = "train.py::evaluate"`) and run `python -m vouch.exec train.py` |
| A single number | `vouch.record("cifar.n_test", 10000, desc="test images")` |
| A dict or table of results | `vouch.record_all(metrics, prefix="cifar.resnet")`, `vouch.table("main", rows, row_key="model")` |
| A claim | `vouch.claim("resnet_wins", vouch.gt(acc_r, acc_v), desc="ResNet beats ViT")` |
| Another language | `vouch run vit_jl --dep src/ -- julia train.jl` (the program writes its values to `$VOUCH_VALUES`) |
| Results that already exist | `vouch import results.json --run eval --producer eval.py` (marked *imported*) |

## 2. Find the key

```console
$ vouch explore --open                  # a local page: script -> function -> keys; one click copies \vouch{...}
$ vouch search vit accuracy cifar       # ranked lookup from the terminal
$ vouch cite evaluate.cifar.vit.acc     # the exact snippet, what it renders as, its subfields
\vouch{evaluate.cifar.vit.acc}         →  90.6 ± 0.4%    (fmt .1pct)
\vouch[.2pct]{evaluate.cifar.vit.acc}  →  90.59 ± 0.44%  (fmt .2pct)
top-1 test accuracy · higher is better · fresh · run experiments.train
subfields: .mean 90.6% · .std 0.4% · .n 5 · .ci95 90.0-91.1% · .min 90.2% · .max 91.1%
```

`.vouch/CATALOG.md`, rewritten by every build, lists every citable key on one line
each: the file an agent reads first.

Long generated keys can get short names in `vouch_values.py`:
`vouch.alias("resnet", "evaluate.cifar.resnet")` makes `\vouch{resnet.acc}` work.

## 3. Cite

```latex
\usepackage{vouch}
...
ResNet reaches \vouch{evaluate.cifar.resnet.acc} top-1 accuracy
(\vouch[.3f]{evaluate.cifar.resnet.acc.mean} as a fraction).
\vouchclaim{resnet_wins}{ResNet outperforms ViT.}
\begin{tabular}{lr} \toprule Model & Acc \\ \midrule \vouchtable{main} \bottomrule \end{tabular}
```

Then build the generated values and compile as usual:

```console
$ vouch build
$ latexmk -pdf paper/main.tex
```

In the draft, every number is a link to a "Value provenance" appendix: what the
value is, the call that produced it (`evaluate(dataset=cifar, model=resnet) over
seed=0..4 (5 calls)`), each seed's result, the run, the command and the commit.
`\usepackage[final]{vouch}` removes all of it for submission.

## 4. Numbers computed from numbers

Differences, ratios, best-of and derived tables live in `vouch_values.py`, never
in anyone's head. `vouch build` evaluates them and records exactly which values
each one read.

```python
import vouch

@vouch.derive("cifar.gap", fmt=".1f", unit="points", desc="ResNet minus ViT, points")
def gap(v):
    return 100 * (v["evaluate.cifar.resnet.acc.mean"] - v["evaluate.cifar.vit.acc.mean"])

@vouch.claim("cifar.big_gap", desc="ResNet leads by more than a point")
def big_gap(v):
    return vouch.gt(v["cifar.gap"], 1)          # "2.3 > 1 (margin 130%)"; thin margins warn
```

`vouch compare A B` does the arithmetic for you (difference, ratio, a Welch t-test
for two means ± std) and prints the `derive` and `claim` that make it citable;
`--write` adds them to `vouch_values.py`.

A number the paper needs before any run has produced it is a placeholder, never a
guess:

```python
vouch.expect("imagenet.vit.acc", desc="ViT top-1 on ImageNet", producer="python train.py --dataset imagenet")
```

The PDF shows `[pending: imagenet.vit.acc]`, `vouch todo` lists what is owed, and
the placeholder resolves once a run records the key.

## 5. Check

```console
$ vouch check
vouch check: paper/main.tex · 16 citations · 1/1 cited runs fresh
✓ OK
```

`vouch check` never runs your code. It fails when:
- a cited key doesn't exist
- the code behind a cited run changed since the run (comments and formatting don't count)
- a claim no longer holds
- a figure is stale
- the generated files are out of date

It warns when a cited value changed since someone last read the sentences around
it (`vouch changes`, then `vouch ack KEY`), and when the paper contains a typed
number:

```
! bare-number    93.2% is typed by hand; it is evaluate.cifar.resnet.acc.mean: cite it as \vouch[.1pct]{...}
! no-source      97.1% matches no recorded value: record it where it is computed, or remove it
```

Converting a paper that already has typed numbers: `vouch suggest` shows, for each
one, the key that prints it (or that nothing does); `vouch suggest --apply` rewrites
the unambiguous ones.

Use it in a pre-commit hook (`vouch hook install`) and in CI:

```yaml
# .github/workflows/paper.yml
name: paper
on: [push, pull_request]
jobs:
  vouch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e path/to/vouch     # wherever vouch comes from
      - run: vouch check --strict
```

## With Claude Code (or another agent)

```console
$ vouch init --agents --yes
```

This installs a skill (`.claude/skills/vouch/SKILL.md`), which teaches the agent to
record, find, cite, compare and wait for results; a short rules block in `CLAUDE.md`;
and a hook that checks each `.tex` file the agent edits. A typed number, a mistyped
key or a broken macro goes straight back to the agent in the same turn, with the
fix. `--stop-gate` also stops the agent from finishing while `vouch check --strict`
fails.

## Commands

| | |
|---|---|
| `vouch init` | set up `vouch.toml`, `.vouch/` and `paper/vouch.sty` |
| `vouch build` | evaluate `vouch_values.py`, write the generated LaTeX, report changes |
| `vouch check [--strict]` | the gate (exit 0 pass, 1 fail, 2 could not check) |
| `vouch explore` | browse every recorded value; copy the LaTeX that cites it |
| `vouch search` / `vouch cite` | find a key by words / the exact snippet to paste |
| `vouch compare A B [--write]` | arithmetic and significance between two values, as citable code |
| `vouch suggest [--apply]` | turn numbers typed into the paper into citations |
| `vouch todo` | values the paper cites that no run has recorded yet |
| `vouch ls` / `vouch trace` / `vouch status` | list keys, see where a value came from, see which runs are stale |
| `vouch changes` / `vouch ack` / `vouch review` | cited values that moved since they were last read |
| `vouch accept RUN --why ...` | a reviewed staleness that doesn't affect the result |
| `vouch run` / `vouch import` | record runs of other languages; register existing results files |
| `vouch sync [--strip]` | write each cited value into a `% vouch:` comment on its line |
| `vouch export --csv FILE` | the provenance table |
| `vouch catalog` | rewrite `.vouch/CATALOG.md` |
| `vouch init --agents` | set up Claude Code: skill, rules, edit hook |

The full design is in [SPEC.md](SPEC.md).
