# Tutorial: from an experiment to a paper whose numbers can't drift

You'll take an ordinary experiment script and a paper draft, and add vouch to
both. By the end, every number in the PDF, the table and the figure are produced
by the code, checked by `vouch check`, and traceable to the call that computed
them. Then you'll change the code and watch vouch point to the sentences that
became false.

The experiment takes about two seconds, so every step can be re-run.

- `start/` is where you begin: a plain experiment (no vouch) and a paper draft.
- `finished/` is where you end: the same project after every step below.

**You need** Python ≥ 3.10 with vouch, numpy and matplotlib, and for the PDF a
LaTeX distribution with `latexmk`. Everything except the PDF works without
LaTeX.

```console
$ pip install -e path/to/vouch numpy matplotlib
$ cp -r examples/tutorial/start my-paper
$ cd my-paper
```

## 0. The experiment

`experiment.py` compares two classifiers on a curved decision boundary with
noisy labels: logistic regression, and k-nearest neighbours with k = 15. Each is
trained on 20 to 640 points, five seeds each. The script prints the mean test
accuracy and saves a learning-curve figure into the paper's folder.

```python
def evaluate(model, n_train, seed=0):
    """Train one model on n_train points; return (test accuracy, train accuracy)."""
    rng = np.random.default_rng(seed)
    x_train, y_train = make_data(n_train, rng)
    x_test, y_test = make_data(N_TEST, rng)
    predict = MODELS[model](x_train, y_train)
    test_acc = float((predict(x_test) == y_test).mean())
    train_acc = float((predict(x_train) == y_train).mean())
    return test_acc, train_acc
```

```console
$ python experiment.py
linear  n=20   test accuracy 0.781 +/- 0.033
linear  n=40   test accuracy 0.788 +/- 0.030
...
   knn  n=640  test accuracy 0.873 +/- 0.014
```

The draft, `paper/main.tex`, has a title, an abstract and two empty sections.
The usual next step is to copy numbers from the terminal into the draft by hand.
That is how papers end up with numbers that no longer match the code, or never
came from it. Instead, the experiment will record its results, and the paper
will cite them.

## 1. Set up vouch

```console
$ vouch init
  wrote vouch.toml (paper: paper/main.tex)
  copied vouch.sty to paper/vouch.sty
  add to the preamble of paper/main.tex:  \usepackage{vouch}
  marked generated files in .gitattributes
```

Add `\usepackage{vouch}` to the preamble of `paper/main.tex`, next to
`graphicx` and `booktabs`.

## 2. Record the results: three lines

```diff
 import matplotlib.pyplot as plt
 from matplotlib.ticker import PercentFormatter
 import numpy as np
+
+import vouch
```

```diff
+@vouch.track(over="seed", returns=("acc", "train_acc"))
 def evaluate(model, n_train, seed=0):
```

```diff
 def main():
+    vouch.params({"sizes": SIZES, "seeds": len(SEEDS), "n_test": N_TEST,
+                  "noise": NOISE, "k": K})
     curves = {}
```

That's the whole change. Nothing else in the script moves:

- **`@vouch.track`** records what `evaluate` returns, every time it is called, under
  a key made of the function's name and its arguments. `evaluate` still returns
  its tuple, so the rest of the script works as before.
  - `over="seed"`: calls that differ only in `seed` are combined into one value,
    mean ± std over the seeds, and each seed's own result is kept.
  - `returns=("acc", "train_acc")` names the two elements of the returned tuple.
    A function that returns a dict needs no `returns=`.
- **`vouch.params`** makes the settings citable. "Five seeds" and "10% label noise"
  in the paper then come from the code, not from memory.
- **The figure** needs no change. vouch sees `savefig` and records the file as
  something this run produced.

Run it again:

```console
$ python experiment.py
...
vouch: 24 key(s) recorded without desc: *.acc (12), *.train_acc (12); describe each family once in vouch.toml, e.g. [metrics] "*.acc" = { desc = "..." }
vouch: recorded run experiment: 36 value(s), 1 artifact(s) → .vouch/runs/experiment.json
```

The run is recorded in `.vouch/runs/experiment.json`: every value, the command,
the commit, the package versions, and a hash of each function that ran. Commit
that file with your code. Here is part of what it holds:

```console
$ vouch ls "*640*"
evaluate.knn.n_train_640.acc           0.873 +/- 0.0141     fresh    cited 0
evaluate.knn.n_train_640.time          0.0964 +/- 0.0207    fresh    cited 0   wall-clock time of one evaluate() call, seconds...
evaluate.knn.n_train_640.train_acc     0.874 +/- 0.00947    fresh    cited 0
evaluate.linear.n_train_640.acc        0.815 +/- 0.0115     fresh    cited 0
...
```

How to read a key: `evaluate.knn.n_train_640.acc` is function `evaluate`, called
with `model="knn"` (a string argument appears as itself) and `n_train=640` (a
number appears with its name). `acc` is the first element of the returned tuple.
`seed` is not in the key; the value is a mean over it. The value also has parts,
`.mean`, `.std`, `.n`, `.ci95`, `.min` and `.max`, and every call's duration is
recorded as `.time`, in seconds.

## 3. Describe the metrics once

vouch asked for descriptions. Describe each family of keys once, in `vouch.toml`:

```toml
[metrics]
"*.acc"       = { fmt = ".1pct", better = "higher", desc = "test accuracy of {1} ({2}), mean and std over seeds" }
"*.train_acc" = { fmt = ".1pct", better = "higher", desc = "training accuracy of {1} ({2}), mean and std over seeds" }
```

`fmt = ".1pct"` prints 0.873 as 87.3%, and `{1}`, `{2}` are key segments. You
don't need to re-run anything: formats and descriptions are applied when values
are read, not when they are recorded.

## 4. Find the key you need

```console
$ vouch explore --open
```

This opens a local page listing every value by script, then function. One click
copies the LaTeX that cites it. From the terminal:

```console
$ vouch search "knn test accuracy 640"
evaluate.knn.n_train_640.acc           87.3 ± 1.4%   test accuracy of knn (n_train_640), mean and std over seeds   (experiment)
evaluate.knn.n_train_640.train_acc     87.4 ± 0.9%   training accuracy of knn (n_train_640), mean and std over se   (experiment)
...
$ vouch cite evaluate.knn.n_train_640.acc
\vouch{evaluate.knn.n_train_640.acc}         →  87.3 ± 1.4%    (fmt .1pct)
\vouch[.2pct]{evaluate.knn.n_train_640.acc}  →  87.30 ± 1.41%    (fmt .2pct)
test accuracy of knn (n_train_640), mean and std over seeds · higher is better · fresh · run experiment
subfields: .mean 87.3% · .std 1.4% · .n 5 · .ci95 85.5-89.1% · .min 85.8% · .max 89.1%
context   "Train one model on n_train points; return (test accuracy, train accuracy)."
```

`evaluate` already had a docstring (it's how `knn`/`linear` are told apart in
this tutorial's `experiment.py`), so `vouch cite` and `vouch search` show it
back as `context:` -- read fresh from the source, a sanity check that
`evaluate.knn.n_train_640.acc` really is what its name suggests, not a
description vouch made up.

After the first `vouch build`, `.vouch/CATALOG.md` lists every key on one line
each. It's the file to give an LLM agent that is writing the paper with you.

## 5. Write the numbers into the paper

Every number is `\vouch{key}`. These are from the Setup and Results sections of
`finished/paper/main.tex`:

| You write | The PDF shows |
|---|---|
| `\vouch{evaluate.knn.n_train_640.acc}` | 87.3 ± 1.4% |
| `\vouch{evaluate.knn.n_train_640.acc.std}` | 1.4% |
| `\vouch{evaluate.linear.n_train_640.train_acc}` | 80.5 ± 1.6% |
| `\vouch{experiment.param.k}` | 15 |
| `\vouch{experiment.param.sizes}` | 20, 40, 80, 160, 320, 640 |
| `\vouch[.0pct]{experiment.param.noise}` | 10% (the optional argument picks a format) |

```latex
We flip \vouch[.0pct]{experiment.param.noise} of the labels at random, ...
We train logistic regression and a \vouch{experiment.param.k}-nearest-neighbour
classifier on \vouch{experiment.param.sizes} points, test each on
\vouch{experiment.param.n_test} fresh points, and repeat every configuration with
\vouch{experiment.param.seeds} seeds.
```

Then build the generated values and compile as usual:

```console
$ vouch build
$ cd paper && latexmk -pdf main.tex
```

`vouch build` writes `paper/vouch-values.tex`, which `vouch.sty` reads. Commit it
too, so co-authors and Overleaf can compile the paper without running anything.

## 6. The figure

Include it as always:

```latex
\includegraphics[width=0.75\linewidth]{figures/learning_curve.pdf}
```

vouch knows which run saved it, and where:

```console
$ vouch trace paper/figures/learning_curve.pdf
paper/figures/learning_curve.pdf   figure
  saved     experiment.py:98   in run experiment (fresh)
  command   python experiment.py
  when      2026-09-19 13:05 UTC
  file      as the run saved it
  cited     paper/main.tex:47
```

`vouch check` fails if the code behind the figure has changed since it was saved,
or if the file no longer matches what the run saved. The caption can cite values
too, like any other text. vouch saves tracked figures without the timestamp
matplotlib normally embeds, so re-running unchanged code gives a byte-identical
file.

## 7. Numbers computed from numbers

"k-NN finishes 5.8 points ahead" is a difference of two recorded values. Don't
work it out by hand: compute it in `vouch_values.py`, next to `vouch.toml`.
`vouch build` evaluates this file. It reads recorded values with `v[key]` and
never runs the experiment.

```python
import vouch

SIZES = "experiment.param.sizes"        # the training set sizes the experiment used


def acc(v, model, n):
    """Test accuracy of one model at one training set size: mean ± std over seeds."""
    return v[f"evaluate.{model}.n_train_{n}.acc"]


@vouch.derive("best_possible", fmt=".0pct", better="higher",
              desc="highest reachable test accuracy, 1 - noise: flipped labels cannot be predicted")
def best_possible(v):
    return 1 - v["experiment.param.noise"]


@vouch.derive("gap", fmt=".1f", unit="points", better="higher",
              desc="k-NN minus linear test accuracy with the most training points, in points")
def gap(v):
    n = max(v[SIZES])
    return 100 * (acc(v, "knn", n).mean - acc(v, "linear", n).mean)
```

`crossover` (the fewest training points from which k-NN stays ahead) is defined
the same way; see `finished/vouch_values.py`.

**Claims.** A sentence such as "with little data the linear model is better" is
a claim. Back it with code that checks it:

```python
@vouch.claim("linear_wins_small", desc="the linear model beats k-NN with the fewest training points")
def linear_wins_small(v):
    n = min(v[SIZES])
    return vouch.gt(acc(v, "linear", n), acc(v, "knn", n))
```

In the paper, `\vouchclaim` wraps the prose that states the claim:

```latex
\vouchclaim{linear_wins_small}{With the fewest training points the linear model
is better}: \vouch{evaluate.linear.n_train_20.acc} against
\vouch{evaluate.knn.n_train_20.acc} for $k$-NN.
```

**Tables.** A table is a function that returns rows:

```python
@vouch.table("learning_curve", columns=["n", "linear", "knn", "gap"], row_key="n",
             fmt={"linear": ".1pct", "knn": ".1pct", "gap": "+.1f"},
             desc="test accuracy by training set size; gap is k-NN minus linear, in points")
def learning_curve(v):
    return [[n, acc(v, "linear", n), acc(v, "knn", n),
             100 * (acc(v, "knn", n).mean - acc(v, "linear", n).mean)]
            for n in sorted(v[SIZES])]
```

You write the `tabular` and its header; `\vouchtable` fills in the rows:

```latex
\begin{tabular}{rccr}
  \toprule
  Training points & Linear & $k$-NN & Gap \\
  \midrule
  \vouchtable{learning_curve}
  \bottomrule
\end{tabular}
```

Every cell can be cited on its own, too: `\vouch{learning_curve.640.gap}` prints
+5.8.

## 8. Check it and read it

```console
$ vouch build
vouch build: evaluated vouch_values.py (6 definitions) -> .vouch/derived.json
vouch build: paper/main.tex
  wrote: paper/vouch-values.tex (284 values, 2 claims, 1 tables; 22 citations)
$ vouch check
vouch check: paper/main.tex · 22 citations · 1/1 cited runs fresh
  · 1 note(s) (--verbose to show)
✓ OK
```

`vouch check` never runs your code, and takes under a second. It passes only if:
- every cited key exists
- every cited value comes from a run whose code hasn't changed since
- every claim holds
- the figure is fresh
- the generated files are up to date

Put it in a pre-commit hook (`vouch hook install`) and in CI (`vouch check --strict`).

In the compiled PDF, every number is a link to a "Value provenance" appendix,
which shows:
- what the number is, and the call that produced it
- each seed's result, and how long each call took
- the command and the commit

`vouch trace` shows the same from the terminal:

```console
$ vouch trace evaluate.knn.n_train_640.acc
evaluate.knn.n_train_640.acc = 0.873 +/- 0.01414213562 (n=5)   → "87.3 +/- 1.4%"   (fmt .1pct)
  desc      test accuracy of knn (n_train_640), mean and std over seeds   · better: higher
  recorded  experiment.py:60   in run experiment
  by        evaluate(model=knn, n_train=640) over seed=0..4 (5 calls)
  time      took 96.4 ± 20.7 ms per call, 482 ms in all
  each call seed=0: 0.858 (98.4 ms); seed=1: 0.882 (125 ms); seed=2: 0.891 (66.3 ms); seed=3: 0.874 (95.6 ms); seed=4: 0.86 (96.9 ms)
  called at experiment.py:78
  command   python experiment.py
  code      6 units in 1 file(s), tracked by function · fresh   (--code to list)
  feeds     crossover (derived) · gap (derived) · knn_wins_large (claim) · learning_curve (table)
  cited     paper/main.tex:36
```

## 9. When the code changes

Change `K = 15` to `K = 1` in `experiment.py`. `vouch check` notices before
anything is re-run:

```console
$ vouch check
vouch check: paper/main.tex · 22 citations · 0/1 cited runs fresh
  ✗ stale          run experiment: experiment.py::<module> changed since the run; the paper cites crossover, gap, experiment.param.noise, best_possible (+14 more)
                   fix: python experiment.py   (or, if the result cannot have changed: vouch accept experiment --why "...")
  ✗ figure-stale   paper/figures/learning_curve.pdf comes from run experiment, which is stale
FAILED: 2 error(s), 0 warning(s)
```

Only real code changes count. Editing a comment or a docstring, or reformatting,
leaves the run fresh. Re-run the experiment, then build:

```console
$ python experiment.py
$ vouch build
  ✗ derive-error   crossover is cited here, but its definition failed (the derive-error at vouch_values.py:30)
  ✗ derive-error   crossover: ValueError: k-NN is not ahead with the most training points (at vouch_values.py:38)

20 CHANGED VALUES — re-read the sentences below

  NOW FALSE   knn_wins_large   HOLDS (0.873 > 0.8146) → FALSE (0.7842 > 0.8146)   (the claim no longer holds)
    paper/main.tex:35  "\vouchclaim{knn_wins_large}{With the most, $k$-NN is better}: it reaches ..."

  NOW FALSE   linear_wins_small   HOLDS (0.7806 > 0.5904) → FALSE (0.7806 > 0.7866)   (the claim no longer holds)
    paper/main.tex:32  "\vouchclaim{linear_wins_small}{With the fewest training points the linear model is better}: ..."

  SUSPICIOUS  gap   5.8 → -3.0   (Δ -8.88, -152.1%; sign flip; large move (152%))
    paper/main.tex:16  "The linear model is better with little data, but $k$-NN has the higher accuracy from ..."

  SUSPICIOUS  table learning_curve   12 cells changed, 8 suspicious
    ! 20.gap   -19.0 → +0.6   (sign flip; large move (103%))
    ! 20.knn   59.0 +/- 10.0% → 78.7 +/- 2.5%   (large move (33%))
    ...
    paper/main.tex:64  "\vouchtable{learning_curve}"
  ...
```

A nearest-neighbour classifier with k = 1 memorises every flipped label. The
paper's two claims are now false, and so is the abstract's premise: k-NN is no
longer ahead with the most training points, so `crossover` has no value. The
PDF would have printed the new numbers without complaint, but the sentences
around them would be wrong. vouch lists those sentences, and `vouch check` now
fails:

```console
$ vouch check
  ✗ derive-error   crossover is cited here, but its definition failed ...
  ✗ false-claim    claim linear_wins_small no longer holds (...): 0.7806 > 0.7866
  ✗ false-claim    claim knn_wins_large no longer holds (...): 0.7842 > 0.8146
  ! suspicious     gap: 5.8 -> -3.0 (POSSIBLE PROBLEM: sign flip; large move (152%))
  ! suspicious     table learning_curve: 12 cell(s) changed, 8 POSSIBLE PROBLEM(s): ...
                   fix: re-read the table and what the text says about it, then: vouch ack learning_curve
  ...
FAILED: 4 error(s), 9 warning(s)
```

After a change like this you have two options:
- **Keep the new result.** Rewrite the sentences and the claims until they are
  true again. Then acknowledge each change you have re-read, with
  `vouch ack KEY`, `vouch ack learning_curve` for a whole table, or
  `vouch review` to step through them. vouch records who acknowledged each change
  and when.
- **Undo the change.** Set `K = 15` again, re-run and rebuild. The values return
  to the ones you had acknowledged, and nothing is pending:

```console
$ python experiment.py && vouch build && vouch check
...
✓ OK
```

## 10. Submitting

`\usepackage[final]{vouch}` removes the links and the provenance appendix, and
leaves plain numbers. `vouch check --strict` still verifies everything, so keep
it in CI.

## Where next

- **Numbers you don't have yet.** `vouch.expect("key", desc=..., producer="python ...")`
  in `vouch_values.py` lets you cite a result before the experiment exists. The
  PDF shows `[pending: key]` and `vouch todo` lists what's owed.
- **Comparisons.** `vouch compare evaluate.knn.n_train_640.acc evaluate.linear.n_train_640.acc`
  prints the difference, a Welch t-test and the `@vouch.derive` and `@vouch.claim`
  that make it citable. `--write` adds them to `vouch_values.py`.
- **No code changes at all.** List the function in `vouch.toml`
  (`[[track]] function = "experiment.py::evaluate"`) and run
  `python -m vouch.exec experiment.py`.
- **Writing with an LLM agent.** `vouch init --agents` installs a Claude Code skill
  and a hook. The hook catches a typed number or a mistyped key the moment the
  agent writes it.
- **Revisiting a script later, or sharing it.** `vouch document experiment.py`
  prints its own docstrings next to the real values it produced -- no paper
  needed. `--md FILE` writes it as a Markdown snapshot.
- The full design is in [SPEC.md](../../SPEC.md).
