# 1. Record the results: three lines

<!-- kept in sync manually with examples/tutorial/README.md; update both -->

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

Full reference: [Recording (Python API)](../guide/recording.md).

**Next:** [2. Describe and find keys](02-describe-and-find.md).
