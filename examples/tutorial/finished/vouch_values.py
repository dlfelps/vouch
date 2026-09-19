"""Numbers the paper computes from the recorded results. `vouch build` evaluates this.

Every input is read with ``v[key]``, so vouch knows what each result depends on and
recomputes it when one of those values changes. Nothing here runs the experiment.
"""

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


@vouch.derive("crossover", desc="fewest training points from which k-NN has the higher mean "
                                "test accuracy at every size tried")
def crossover(v):
    sizes = sorted(v[SIZES])
    ahead = [acc(v, "knn", n).mean > acc(v, "linear", n).mean for n in sizes]
    for i, n in enumerate(sizes):
        if all(ahead[i:]):
            return n
    raise ValueError("k-NN is not ahead with the most training points")


@vouch.claim("linear_wins_small", desc="the linear model beats k-NN with the fewest training points")
def linear_wins_small(v):
    n = min(v[SIZES])
    return vouch.gt(acc(v, "linear", n), acc(v, "knn", n))


@vouch.claim("knn_wins_large", desc="k-NN beats the linear model with the most training points")
def knn_wins_large(v):
    n = max(v[SIZES])
    return vouch.gt(acc(v, "knn", n), acc(v, "linear", n))


@vouch.table("learning_curve", columns=["n", "linear", "knn", "gap"], row_key="n",
             fmt={"linear": ".1pct", "knn": ".1pct", "gap": "+.1f"},
             desc="test accuracy by training set size; gap is k-NN minus linear, in points")
def learning_curve(v):
    return [[n, acc(v, "linear", n), acc(v, "knn", n),
             100 * (acc(v, "knn", n).mean - acc(v, "linear", n).mean)]
            for n in sorted(v[SIZES])]
