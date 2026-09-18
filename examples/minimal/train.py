"""A toy experiment: two classifiers on synthetic data, five seeds each.

Run it normally -- ``python train.py`` -- and vouch records every result.
"""

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
