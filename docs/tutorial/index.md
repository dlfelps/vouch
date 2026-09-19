# Tutorial: from an experiment to a paper whose numbers can't drift

<!-- kept in sync manually with examples/tutorial/README.md; update both -->

You'll take an ordinary experiment script and a paper draft, and add vouch to
both. By the end, every number in the PDF, the table and the figure are produced
by the code, checked by `vouch check`, and traceable to the call that computed
them. Then you'll change the code and watch vouch point to the sentences that
became false.

The experiment takes about two seconds, so every step can be re-run.

- `start/` is where you begin: a plain experiment (no vouch) and a paper draft.
- `finished/` is where you end: the same project after every step below.

Both live in [`examples/tutorial/`](https://github.com/dlfelps/vouch/tree/main/examples/tutorial)
in the repository.

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

**Next:** [1. Record results](01-record.md).
