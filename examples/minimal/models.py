"""Two tiny classifiers and a synthetic dataset -- just enough to have real results."""

import random


def make_data(seed, n_train=400, n_test=200, gap=1.2):
    """Two overlapping Gaussian blobs in 2-D; label 1 is shifted by ``gap``."""
    rng = random.Random(seed)

    def draw(n):
        pts = []
        for _ in range(n):
            y = rng.random() < 0.5
            pts.append(((rng.gauss(gap * y, 1.0), rng.gauss(gap * y, 1.0)), int(y)))
        return pts
    return draw(n_train), draw(n_test)


def nearest_centroid(train):
    sums = {0: [0.0, 0.0, 0], 1: [0.0, 0.0, 0]}
    for (a, b), y in train:
        s = sums[y]
        s[0] += a
        s[1] += b
        s[2] += 1
    cent = {y: (s[0] / s[2], s[1] / s[2]) for y, s in sums.items()}

    def predict(x):
        return min(cent, key=lambda y: (x[0] - cent[y][0]) ** 2 + (x[1] - cent[y][1]) ** 2)
    return predict


def majority(train):
    ones = sum(y for _, y in train)
    label = int(ones * 2 >= len(train))
    return lambda x: label
