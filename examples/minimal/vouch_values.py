"""Numbers computed from the recorded results. `vouch build` evaluates this file.

Nothing here is typed by hand or computed in anyone's head: every value is read
from a run with ``v[key]``, and vouch records which keys each result read.
"""

import vouch

# cite the centroid results by a shorter name: \vouch{centroid.acc}
vouch.alias("centroid", "toy.centroid")


@vouch.derive("toy.gain", fmt=".1f", unit="points", better="higher",
              desc="nearest-centroid minus majority mean accuracy, in percentage points")
def gain(v):
    return 100 * (v["centroid.acc.mean"] - v["toy.majority.acc.mean"])


@vouch.claim("toy.gain_over_10", desc="nearest-centroid beats the baseline by over 10 points")
def gain_over_10(v):
    return vouch.gt(v["toy.gain"], 10)


@vouch.table("summary", columns=["model", "acc", "gain"], row_key="model",
             fmt={"gain": ".1f"}, highlight={"acc": "max"},
             desc="accuracy and gain over the majority baseline, by model")
def summary(v):
    base = v["toy.majority.acc.mean"]
    return [[m, v[f"toy.{m}.acc"], 100 * (v[f"toy.{m}.acc.mean"] - base)]
            for m in ("centroid", "majority")]
