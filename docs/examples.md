# Examples

Both examples live in the repository under
[`examples/`](https://github.com/dlfelps/vouch/tree/main/examples).

## `examples/tutorial/`

The project used throughout the [tutorial](tutorial/index.md): a two-classifier
learning-curve experiment and a paper draft.

- [`start/`](https://github.com/dlfelps/vouch/tree/main/examples/tutorial/start) — the plain experiment and paper, before vouch.
- [`finished/`](https://github.com/dlfelps/vouch/tree/main/examples/tutorial/finished) — the same project after every tutorial step: `@vouch.track`, `vouch.params`, `vouch_values.py` with derived values, claims and a table, and a paper fully cited with `\vouch`.

```console
$ cp -r examples/tutorial/start my-paper
$ cd my-paper
```

## `examples/minimal/`

A smaller, single-file worked example: two toy classifiers, five seeds, one
`record_all` call, one derived table and one claim.

```python
import vouch
from models import majority, make_data, nearest_centroid

SEEDS = range(5)
N_TEST = 200


def evaluate(model, seed):
    train, test = make_data(seed, n_test=N_TEST)
    predict = model(train)
    return sum(predict(x) == y for x, y in test) / len(test)


results = {}
for name, model in [("centroid", nearest_centroid), ("majority", majority)]:
    results[name] = {"acc": vouch.Stat.of([evaluate(model, s) for s in SEEDS]),
                     "n_test": N_TEST}

vouch.params({"seeds": len(SEEDS), "n_train": 400})
vouch.record_all(results, prefix="toy")
vouch.table("main", [{"model": m, "acc": r["acc"]} for m, r in results.items()],
            row_key="model", highlight={"acc": "max"}, desc="accuracy by model")
vouch.claim("toy.centroid_beats_majority",
            results["centroid"]["acc"].mean > results["majority"]["acc"].mean,
            desc="nearest-centroid has higher mean accuracy than the majority baseline",
            values={"centroid": results["centroid"]["acc"].mean,
                    "majority": results["majority"]["acc"].mean})
```

Good for seeing the whole shape of a vouch-instrumented experiment — record,
derive, claim, table — in one screen.

## `examples/viewer-check/`

A one-page LaTeX document that compiles vouch's three provenance modes
(`link`, `tooltip`, `note`) side by side, for checking which ones your team's
PDF viewers actually render — see [LaTeX interface](guide/latex.md#from-a-number-to-its-provenance-in-the-pdf).
