import pytest

from vouch.render import Options, render, tex_escape
from vouch.values import Stat

S = Stat(0.93214, 0.0041, 5)


# SPEC §6.2, row by row
@pytest.mark.parametrize("value,spec,unit,latex", [
    (0.93214, ".1pct", None, r"93.2\%"),
    (0.93214, ".2f", None, r"0.93"),
    (18535, ",d", None, r"18{,}535"),
    (1.87e-14, ".1e", None, r"\ensuremath{1.9\times10^{-14}}"),
    (1.6247, ".2fx", None, r"\ensuremath{1.62\times}"),
    (-0.9212, ".2f", None, r"\ensuremath{-0.92}"),
    (0.0213, "+.1pct", None, r"\ensuremath{+2.1}\%"),
    (S, ".1pct", None, r"\ensuremath{93.2 \pm 0.4}\%"),
    (S, ".1pctci", None, r"93.2\% [92.7, 93.7]"),
    ((41.2, 53.4), ".0f", None, r"41--53"),
    ((56, 32), "dto", None, r"\ensuremath{56 \to 32}"),
    (12.34, ".1fu", "ms", r"12.3\,ms"),
    ("ResNet-50", "s", None, r"ResNet-50"),
])
def test_spec_table(value, spec, unit, latex):
    assert render(value, spec, unit=unit).latex == latex


@pytest.mark.parametrize("value,spec,latex", [
    (0.125, ".2f", "0.13"),          # half-up on the decimal repr, not banker's
    (2.675, ".2f", "2.68"),          # binary 2.67499999... would round down in format()
    (0.5, ".0f", "1"),
    (-0.001, ".2f", "0.00"),         # never -0.00
    (-0.0, ".1f", "0.0"),
    (9.96, ".1e", r"\ensuremath{1.0\times10^{1}}"),
    (93.214, ".3g", "93.2"),
    (0.0001234, ".3g", "0.000123"),
    (123456.0, ".3g", r"\ensuremath{1.23\times10^{5}}"),
    (2.0, ".3g", "2"),
    (1234567, ",d", "1{,}234{,}567"),
    (3.7, "d", "4"),
])
def test_rounding_and_types(value, spec, latex):
    assert render(value, spec).latex == latex


def test_half_even_option():
    assert render(0.125, ".2f", opts=Options(rounding="half_even")).latex == "0.12"


def test_defaults_by_type():
    assert render(42).latex == "42"
    assert render(0.93214).latex == "0.932"            # default_float .3g
    assert render(True).latex == "true"
    assert render((1, 2)).latex == "1--2"
    assert render(0.93214, opts=Options(default_float=".2f")).latex == "0.93"


def test_named_formats():
    opts = Options(named={"pct1": ".1pct"})
    assert render(0.5, "pct1", opts=opts).latex == r"50.0\%"


def test_nonfinite():
    assert render(float("nan"), ".2f").latex == "NaN"
    assert render(float("inf"), ".2f").latex == r"\ensuremath{\infty}"
    assert render(float("-inf"), ".2f").latex == r"\ensuremath{-\infty}"


def test_plain_forms_are_pdf_and_comment_safe():
    assert render(0.93214, ".1pct").plain == r"93.2\%"
    assert render(S, ".1pct").plain == r"93.2 +/- 0.4\%"
    assert render(1.87e-14, ".1e").plain == "1.9e-14"
    assert render((56, 32), "dto").plain == "56 -> 32"
    assert render(-0.92, ".2f").plain == "-0.92"


def test_tuples_and_ci_edge_cases():
    assert render((1.0, 2.0, 3.0), ".1f").latex == "1.0, 2.0, 3.0"
    assert render((-1.0, 2.0), ".0f").latex == r"\ensuremath{-1}--2"
    assert render((0.41, 0.53), ".0pct").latex == r"41--53\%"
    assert render(Stat(0.9, 0.0, 1), ".1pctci").latex == r"90.0\%"   # n=1: no interval


def test_siunitx():
    si = Options(siunitx=True)
    assert render(0.93214, ".1pct", opts=si).latex == r"\qty{93.2}{\percent}"
    assert render(18535, ",d", opts=si).latex == r"\num{18535}"
    assert render(1.87e-14, ".1e", opts=si).latex == r"\num{1.9e-14}"
    assert render(12.34, ".1fu", unit="ms", opts=si).latex == r"\qty{12.3}{ms}"
    assert render(S, ".1pct", opts=si).latex == r"\qty{93.2 +- 0.4}{\percent}"
    assert render((41.2, 53.4), ".0f", opts=si).latex == r"\numrange{41}{53}"


def test_tex_escape():
    assert tex_escape("a_b & 50% #1 {x} ~ ^ \\") == \
        r"a\_b \& 50\% \#1 \{x\} \textasciitilde{} \textasciicircum{} \textbackslash{}"
    assert render("resnet_50", "s").latex == r"resnet\_50"
