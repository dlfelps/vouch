# 4. Numbers computed from numbers

<!-- kept in sync manually with examples/tutorial/README.md; update both -->

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

Full reference: [Derived values, claims, tables](../guide/derived-values.md).

**Next:** [5. Check it and read it](05-check.md).
