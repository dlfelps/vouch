"""The value model: key grammar, ``Stat``, and how values are stored as JSON.

A key is what a value is recorded under *and* what the paper cites, so it has to
be safe everywhere it travels: a JSON key, a filename (run ids share the grammar),
and a ``\\csname`` in LaTeX. Hence the narrow alphabet (SPEC §2.1).

Values are stored with an explicit ``type`` so a record reads back exactly: an
``int`` stays an int, a ``Stat`` stays a Stat, and a NaN survives a format (JSON)
that has no way to spell it.
"""

from __future__ import annotations

import dataclasses
import math
import numbers
import re
import statistics
from typing import Any, Iterable, Mapping

KEY_MAX = 128
_KEY_RE = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
_BAD_CHAR = re.compile(r"[^A-Za-z0-9_.-]")

BETTER = ("higher", "lower")


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

def is_valid_key(key: object) -> bool:
    return isinstance(key, str) and len(key) <= KEY_MAX and _KEY_RE.fullmatch(key) is not None


def sanitize_key(raw: object) -> str:
    """The nearest valid key to ``raw``.

    ``/`` becomes ``.`` because that is how W&B and TensorBoard nest metric names
    (``eval/accuracy``); anything else outside the alphabet becomes ``_``.
    """
    s = str(raw).strip().replace("/", ".").replace("\\", ".")
    s = _BAD_CHAR.sub("_", s)
    s = re.sub(r"\.{2,}", ".", s).strip(".")
    return s[:KEY_MAX] or "_"


_SEGMENT_BAD = re.compile(r"[^A-Za-z0-9_-]")


def slug_segment(raw: object) -> str:
    """One key segment from a row or column name: ``0.1`` -> ``0_1``, ``ResNet 50`` -> ``ResNet_50``.

    Table cells are keyed ``<table>.<row>.<col>``, so a dot inside a row name
    would silently add a level; it has to become part of a single segment.
    """
    return _SEGMENT_BAD.sub("_", str(raw).strip()) or "_"


def join_key(*parts: str | None) -> str:
    return ".".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Stat
# ---------------------------------------------------------------------------

# Two-sided 97.5% Student-t quantiles for df = 1..30; beyond that the normal
# quantile is within half a percent.
_T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
         2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
         2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042)


def t975(df: int) -> float:
    if df < 1:
        raise ValueError("t quantile needs df >= 1")
    return _T975[df - 1] if df <= len(_T975) else 1.960


@dataclasses.dataclass(frozen=True)
class Stat:
    """A summary of repeated measurements: mean, sample std (ddof=1), n.

    Cited as ``mean \\pm std`` by default; the subfields ``.mean .std .n .ci95
    .min .max`` are citable on their own (SPEC §2.1).
    """

    mean: float
    std: float
    n: int
    min: float | None = None
    max: float | None = None

    @classmethod
    def of(cls, samples: Iterable[Any]) -> "Stat":
        xs = [float(coerce_scalar(x)) for x in samples]
        if not xs:
            raise ValueError("Stat.of() needs at least one sample")
        std = statistics.stdev(xs) if len(xs) > 1 else 0.0
        return cls(mean=statistics.fmean(xs), std=std, n=len(xs), min=min(xs), max=max(xs))

    @property
    def ci95(self) -> tuple[float, float] | None:
        """Student-t 95% interval for the mean; None when n < 2."""
        if self.n < 2:
            return None
        half = t975(self.n - 1) * self.std / math.sqrt(self.n)
        return (self.mean - half, self.mean + half)

    def field(self, name: str) -> Any:
        if name == "ci95":
            return self.ci95
        if name in ("mean", "std", "n", "min", "max"):
            return getattr(self, name)
        raise KeyError(name)


STAT_FIELDS = ("mean", "std", "n", "ci95", "min", "max")


# ---------------------------------------------------------------------------
# scalars from foreign libraries
# ---------------------------------------------------------------------------

def coerce_scalar(value: Any) -> Any:
    """Unwrap one-element numpy arrays/scalars and torch tensors via ``.item()``.

    Python scalars, strings and containers pass through untouched. Anything with
    more than one element is returned as is, for the caller to reject.
    """
    if isinstance(value, (bool, int, float, str, Stat)) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (Mapping, list, tuple)):
        size = getattr(value, "size", None)
        if callable(size):                       # torch: .size() is a method
            size = None
        if size is None:
            numel = getattr(value, "numel", None)
            size = numel() if callable(numel) else 1
        if size == 1:
            try:
                return item()
            except (TypeError, ValueError):
                return value
    return value


# ---------------------------------------------------------------------------
# JSON encoding
# ---------------------------------------------------------------------------

def _enc_float(x: float) -> Any:
    if math.isnan(x):
        return {"$float": "nan"}
    if math.isinf(x):
        return {"$float": "inf" if x > 0 else "-inf"}
    return x


def _dec_float(x: Any) -> float:
    if isinstance(x, dict) and "$float" in x:
        return float(x["$float"])
    return float(x)


def encode(value: Any) -> tuple[str, Any]:
    """(type, JSON-safe payload) for a recordable value; raises TypeError otherwise."""
    value = coerce_scalar(value)
    if isinstance(value, bool):
        return "bool", value
    if isinstance(value, numbers.Integral):
        return "int", int(value)
    if isinstance(value, numbers.Real):
        return "float", _enc_float(float(value))
    if isinstance(value, str):
        return "str", value
    if isinstance(value, Stat):
        payload = {"mean": _enc_float(float(value.mean)), "std": _enc_float(float(value.std)),
                   "n": int(value.n)}
        if value.min is not None:
            payload["min"] = _enc_float(float(value.min))
        if value.max is not None:
            payload["max"] = _enc_float(float(value.max))
        return "stat", payload
    if isinstance(value, (tuple, list)):
        items = [coerce_scalar(v) for v in value]
        if items and all(isinstance(v, numbers.Real) and not isinstance(v, bool) for v in items):
            return "tuple", [int(v) if isinstance(v, numbers.Integral) else _enc_float(float(v))
                             for v in items]
    raise TypeError(f"cannot record a value of type {type(value).__name__}")


def decode(kind: str, payload: Any) -> Any:
    if kind == "bool":
        return bool(payload)
    if kind == "int":
        return int(payload)
    if kind == "float":
        return _dec_float(payload)
    if kind == "str":
        return str(payload)
    if kind == "stat":
        return Stat(mean=_dec_float(payload["mean"]), std=_dec_float(payload["std"]),
                    n=int(payload["n"]),
                    min=_dec_float(payload["min"]) if "min" in payload else None,
                    max=_dec_float(payload["max"]) if "max" in payload else None)
    if kind == "tuple":
        return tuple(v if isinstance(v, int) else _dec_float(v) for v in payload)
    raise ValueError(f"unknown value type {kind!r}")


def is_finite_value(value: Any) -> bool:
    value = coerce_scalar(value)
    if isinstance(value, bool) or isinstance(value, str):
        return True
    if isinstance(value, numbers.Real):
        return math.isfinite(float(value))
    if isinstance(value, Stat):
        return math.isfinite(value.mean) and math.isfinite(value.std)
    if isinstance(value, (tuple, list)):
        return all(is_finite_value(v) for v in value)
    return True


def is_number(value: Any) -> bool:
    value = coerce_scalar(value)
    return (isinstance(value, numbers.Real) and not isinstance(value, bool)) or isinstance(value, Stat)


def encode_cell(value: Any) -> Any:
    """A table cell or parameter as plain JSON where possible.

    Plain numbers, strings, bools and ``None`` stay plain so the record reads
    naturally; a Stat or tuple is tagged so it decodes back to what it was.
    """
    value = coerce_scalar(value)
    if value is None:
        return None
    kind, payload = encode(value)
    if kind == "stat":
        return {"$stat": payload}
    if kind == "tuple":
        return {"$tuple": payload}
    return payload


def decode_cell(payload: Any) -> Any:
    if isinstance(payload, dict):
        if "$stat" in payload:
            return decode("stat", payload["$stat"])
        if "$tuple" in payload:
            return decode("tuple", payload["$tuple"])
        if "$float" in payload:
            return _dec_float(payload)
    return payload


def encode_param(value: Any) -> Any:
    """Parameters accept anything: recordable values are encoded, the rest become strings."""
    value = coerce_scalar(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return encode_cell(value) if isinstance(value, float) else value
    try:
        return encode_cell(value)
    except TypeError:
        if isinstance(value, (list, tuple)):
            return [encode_param(v) for v in value]
        return str(value)


# ---------------------------------------------------------------------------
# flattening nested containers (params, record_all)
# ---------------------------------------------------------------------------

def as_mapping(obj: Any) -> Mapping | None:
    """A plain mapping view of dict-like objects, or None if ``obj`` isn't one."""
    if isinstance(obj, Mapping):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    for name in ("to_container", "to_dict", "model_dump", "dict"):   # OmegaConf, pandas Series, Pydantic
        meth = getattr(obj, name, None)
        if callable(meth):
            try:
                out = meth()
            except TypeError:
                continue
            if isinstance(out, Mapping):
                return out
    if hasattr(obj, "__dict__") and type(obj).__name__ == "Namespace":   # argparse
        return vars(obj)
    return None


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Nested mappings flattened to dotted keys; leaves are left as they are."""
    out: dict[str, Any] = {}
    mapping = as_mapping(obj)
    if mapping is None:
        out[prefix] = obj
        return out
    for k, v in mapping.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if as_mapping(v) is not None and not isinstance(v, Stat):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out
