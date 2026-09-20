"""Docstring extraction (SPEC §12.2, §13.2): read fresh, never affects staleness."""

from vouch import docextract

SRC = '''
"""Learning curves: linear vs k-NN."""

import numpy as np


def top_level():
    """Not tracked, but still documented."""
    return 1


class Model:
    """A classifier family."""

    def fit(self, x, y):
        """Fit on (x, y)."""
        return self

    def predict(self, x):
        return x


def evaluate(dataset, model, seed=0):
    """Train one model; return (test accuracy, train accuracy).

    A second paragraph that must not appear in the extracted line.
    """
    if seed:
        def inner():
            """A conditionally-defined nested function."""
            return 1
        return inner()
    return 0


def undocumented():
    return 0
'''


def test_extracts_module_function_and_class_docstrings():
    docs = docextract.extract(SRC, "experiment.py")
    assert docs[docextract.MODULE] == "Learning curves: linear vs k-NN."
    assert docs["top_level"] == "Not tracked, but still documented."
    assert docs["Model"] == "A classifier family."
    assert docs["Model.fit"] == "Fit on (x, y)."
    assert "predict" not in {k.rsplit(".", 1)[-1] for k in docs if k.startswith("Model.predict")}


def test_takes_only_the_first_line_of_a_multiline_docstring():
    docs = docextract.extract(SRC, "experiment.py")
    assert docs["evaluate"] == "Train one model; return (test accuracy, train accuracy)."
    assert "second paragraph" not in docs["evaluate"]


def test_walks_into_conditional_defs():
    docs = docextract.extract(SRC, "experiment.py")
    assert docs["evaluate.inner"] == "A conditionally-defined nested function."


def test_no_entry_for_an_undocumented_function():
    docs = docextract.extract(SRC, "experiment.py")
    assert "undocumented" not in docs


def test_unparsable_source_returns_empty():
    assert docextract.extract("def f(:\n", "bad.py") == {}


def test_context_for_reads_the_current_source(tmp_path):
    (tmp_path / "exp.py").write_text(SRC, encoding="utf-8")
    assert (docextract.context_for("exp.py::evaluate", tmp_path)
            == "Train one model; return (test accuracy, train accuracy).")
    assert docextract.context_for("exp.py::undocumented", tmp_path) is None


def test_context_for_never_raises_on_a_missing_file(tmp_path):
    assert docextract.context_for("nope.py::f", tmp_path) is None


def test_context_for_rejects_a_malformed_ref(tmp_path):
    assert docextract.context_for("not-a-ref", tmp_path) is None
    assert docextract.context_for("", tmp_path) is None


def test_context_for_reuses_a_shared_cache(tmp_path):
    (tmp_path / "exp.py").write_text(SRC, encoding="utf-8")
    cache: dict = {}
    docextract.context_for("exp.py::evaluate", tmp_path, cache)
    assert "exp.py" in cache
    (tmp_path / "exp.py").write_text("def evaluate(): pass\n", encoding="utf-8")
    # stale cache still answers from the first read -- callers scope it per lookup batch
    assert (docextract.context_for("exp.py::evaluate", tmp_path, cache)
            == "Train one model; return (test accuracy, train accuracy).")
