import argparse
import dataclasses
import math

import pytest

from vouch.values import (Stat, coerce_scalar, decode, decode_cell, encode, encode_cell,
                          encode_param, flatten, is_valid_key, sanitize_key)


@pytest.mark.parametrize("key", ["acc", "cifar.resnet.acc", "a-b_c.D9", "x" * 128])
def test_valid_keys(key):
    assert is_valid_key(key)


@pytest.mark.parametrize("key", ["", "a..b", ".a", "a.", "CIFAR acc", "eval/acc", "x" * 129, 3, None])
def test_invalid_keys(key):
    assert not is_valid_key(key)


@pytest.mark.parametrize("raw,fixed", [
    ("eval/accuracy", "eval.accuracy"),
    ("CIFAR acc", "CIFAR_acc"),
    ("a..b", "a.b"),
    ("/lead/trail/", "lead.trail"),
    ("top-1 (%)", "top-1____"),
])
def test_sanitize(raw, fixed):
    assert sanitize_key(raw) == fixed
    assert is_valid_key(sanitize_key(raw))


def test_stat_of_and_ci():
    s = Stat.of([0.93, 0.935, 0.931, 0.928, 0.936])
    assert s.n == 5 and s.min == 0.928 and s.max == 0.936
    assert math.isclose(s.mean, 0.932)
    lo, hi = s.ci95
    assert lo < s.mean < hi
    # half-width = t(0.975, 4) * std / sqrt(5)
    assert math.isclose(hi - s.mean, 2.776 * s.std / math.sqrt(5))
    assert Stat.of([1.0]).ci95 is None and Stat.of([1.0]).std == 0.0


@pytest.mark.parametrize("value,kind", [
    (True, "bool"), (3, "int"), (0.5, "float"), ("ResNet-50", "str"),
    ((41.2, 53.4), "tuple"), (Stat(0.9, 0.01, 5), "stat"),
])
def test_encode_roundtrip(value, kind):
    k, payload = encode(value)
    assert k == kind
    assert decode(k, payload) == value


def test_nonfinite_roundtrip():
    for x in (math.nan, math.inf, -math.inf):
        k, payload = encode(x)
        assert payload == {"$float": str(x)}
        back = decode(k, payload)
        assert (math.isnan(back) and math.isnan(x)) or back == x


def test_encode_rejects_non_scalars():
    for bad in ([[1, 2]], {"a": 1}, object(), ["a", "b"]):
        with pytest.raises(TypeError):
            encode(bad)


class FakeArray:
    """Stands in for a numpy scalar / 1-element array."""
    def __init__(self, v, size=1):
        self.v, self.size = v, size

    def item(self):
        return self.v


class FakeTensor:
    """Stands in for torch: .size() is a method and numel() gives the count."""
    def __init__(self, v, n=1):
        self.v, self.n = v, n

    def size(self):
        return (self.n,)

    def numel(self):
        return self.n

    def item(self):
        return self.v


def test_coerce_scalar_unwraps_foreign_scalars():
    assert coerce_scalar(FakeArray(0.5)) == 0.5
    assert coerce_scalar(FakeTensor(7)) == 7
    big = FakeArray(0.5, size=10)
    assert coerce_scalar(big) is big
    assert encode(FakeTensor(2))[0] == "int"


def test_cells_and_params():
    assert encode_cell(0.5) == 0.5 and encode_cell(None) is None
    assert decode_cell(encode_cell(Stat(1.0, 0.1, 3))) == Stat(1.0, 0.1, 3)
    assert decode_cell(encode_cell((1, 2))) == (1, 2)
    assert encode_param(math.nan) == {"$float": "nan"}
    assert encode_param(["a", 1]) == ["a", 1]
    assert encode_param(object).startswith("<class")


def test_flatten():
    @dataclasses.dataclass
    class Opt:
        lr: float = 0.1
        betas: tuple = (0.9, 0.99)

    ns = argparse.Namespace(model="resnet", optim=Opt())
    assert flatten({"a": {"b": 1, "c": {"d": 2}}, "e": 3}) == {"a.b": 1, "a.c.d": 2, "e": 3}
    flat = flatten(ns)
    assert flat == {"model": "resnet", "optim.lr": 0.1, "optim.betas": (0.9, 0.99)}
    # a Stat is a leaf, not a dataclass to flatten
    assert flatten({"acc": Stat(1.0, 0.0, 1)}) == {"acc": Stat(1.0, 0.0, 1)}
