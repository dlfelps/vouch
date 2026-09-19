"""Learning curves: a linear model against k-nearest neighbours.

Both classifiers learn a curved decision boundary from noisy labels. We measure
test accuracy as the training set grows, over five seeds, and plot the curves.

    python experiment.py        # about two seconds; needs numpy and matplotlib
"""

import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np

import vouch

SIZES = [20, 40, 80, 160, 320, 640]     # training set sizes
SEEDS = range(5)
N_TEST = 1000                           # test points per seed
NOISE = 0.1                             # fraction of labels flipped at random
K = 15                                  # neighbours consulted by k-NN


def make_data(n, rng):
    """Points in the square [-1, 1]^2, labelled by which side of a sine curve they
    fall on; a fraction NOISE of the labels is flipped."""
    x = rng.uniform(-1, 1, size=(n, 2))
    y = (x[:, 1] > 0.6 * np.sin(3 * x[:, 0])).astype(int)
    flip = rng.random(n) < NOISE
    return x, np.where(flip, 1 - y, y)


def linear(x_train, y_train, steps=300, lr=1.0):
    """Logistic regression, fitted by gradient descent. Returns a predictor."""
    def design(x):
        return np.c_[x, np.ones(len(x))]
    X, w = design(x_train), np.zeros(3)
    for _ in range(steps):
        p = 1 / (1 + np.exp(-X @ w))
        w -= lr * X.T @ (p - y_train) / len(X)
    return lambda x: (design(x) @ w > 0).astype(int)


def knn(x_train, y_train, k=K):
    """Majority vote of the k nearest training points. Returns a predictor."""
    def predict(x):
        dist = ((x[:, None, :] - x_train[None, :, :]) ** 2).sum(axis=-1)
        nearest = np.argpartition(dist, k - 1, axis=1)[:, :k]
        return (y_train[nearest].mean(axis=1) > 0.5).astype(int)
    return predict


MODELS = {"linear": linear, "knn": knn}


@vouch.track(over="seed", returns=("acc", "train_acc"))
def evaluate(model, n_train, seed=0):
    """Train one model on n_train points; return (test accuracy, train accuracy)."""
    rng = np.random.default_rng(seed)
    x_train, y_train = make_data(n_train, rng)
    x_test, y_test = make_data(N_TEST, rng)
    predict = MODELS[model](x_train, y_train)
    test_acc = float((predict(x_test) == y_test).mean())
    train_acc = float((predict(x_train) == y_train).mean())
    return test_acc, train_acc


def main():
    vouch.params({"sizes": SIZES, "seeds": len(SEEDS), "n_test": N_TEST,
                  "noise": NOISE, "k": K})
    curves = {}
    for model in MODELS:
        for n in SIZES:
            accs = [evaluate(model, n, seed)[0] for seed in SEEDS]
            curves[model, n] = (statistics.mean(accs), statistics.stdev(accs))
            print(f"{model:>6}  n={n:<4d} test accuracy {curves[model, n][0]:.3f} "
                  f"+/- {curves[model, n][1]:.3f}")

    fig, ax = plt.subplots(figsize=(4.5, 3))
    for model, label in (("linear", "linear model"), ("knn", f"{K}-NN")):
        mean = np.array([curves[model, n][0] for n in SIZES])
        std = np.array([curves[model, n][1] for n in SIZES])
        ax.plot(SIZES, mean, marker="o", label=label)
        ax.fill_between(SIZES, mean - std, mean + std, alpha=0.2)
    ax.axhline(1 - NOISE, color="gray", linestyle="--", linewidth=1, label="best possible")
    ax.set_xscale("log", base=2)
    ax.set_xticks(SIZES, [str(n) for n in SIZES])
    ax.set_xlabel("training points")
    ax.set_ylabel("test accuracy")
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.legend(frameon=False)
    fig.tight_layout()
    Path("paper/figures").mkdir(parents=True, exist_ok=True)
    fig.savefig("paper/figures/learning_curve.pdf")


if __name__ == "__main__":
    main()
