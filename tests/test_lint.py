"""The bare-number lint (SPEC §11.1): typed numbers, matched against recorded values."""

import pytest

from vouch.check import run_check
from vouch.config import Config
from vouch.tex.lint import find_literals, mask, parse_numbers, rounds_to
from vouch.tex.scan import scan

EXP = '''
    import vouch

    vouch.params({"seeds": 10, "layers": 38})
    vouch.record("cifar.resnet.acc", vouch.Stat.of([0.931, 0.932, 0.933]), fmt=".1pct",
                 desc="accuracy")
    vouch.record("cifar.n_images", 18535, desc="images")
    vouch.record("speedup", 2.46, desc="speed-up")
    vouch.record("lr", 0.0012, desc="learning rate")
'''


@pytest.fixture
def proj(project):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n')
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    return project


def paper(project, body: str, preamble: str = "") -> None:
    project.write("paper/main.tex", "\\documentclass{article}\n\\usepackage{vouch}\n" + preamble
                  + "\\begin{document}\n" + body + "\n\\end{document}\n")


def lint_issues(project, strict=False):
    from vouch.build import build
    build(Config.load(project.root))
    rep = run_check(Config.load(project.root), strict=strict, check_env=False)
    return [i for i in rep.issues if i.check in ("bare-number", "no-source")]


def literals(project, body, preamble=""):
    paper(project, body, preamble)
    doc = scan(project.root / "paper/main.tex", project.root)
    return [lit.text for lit in find_literals(doc)]


def test_what_is_flagged(project):
    body = r"""
Accuracy is 93.2\% on 18{,}535 images, with loss 0.93 and lr $1.2\times10^{-3}$.
It is 2.5\times faster, at $91.2 \pm 0.4$, over 38 layers; in math $x = 0.5$.
\vouchclaim{k}{a claim whose prose says 42 things}.
"""
    assert literals(project, body) == [
        r"93.2\%", "18{,}535", "0.93", r"1.2\times10^{-3}", r"2.5\times", r"91.2 \pm 0.4",
        "38", "0.5", "42"]


def test_what_is_not_flagged(project):
    body = r"""
\section{Results}\label{sec:3} See Figure~\ref{fig:2} and \cite{smith2020,lee21}.
\begin{table}[h]\begin{tabular}{lrr}\toprule a & b \\[2pt] \midrule\end{tabular}\end{table}
\includegraphics[width=0.5\linewidth]{figs/fig1} \vspace{3pt} \hspace{1.5em}
In 2024 we used ResNet-50, GPT4 and 3 models on a 2\times2 grid; Python v1.2.3.
$x_{10} + \sum_{i=1}^{10} y_i$ \vouch{cifar.resnet.acc} \vouch[.3f]{lr} % 99 in a comment
\begin{minipage}{0.45\textwidth}text\end{minipage} \multicolumn{3}{c}{head}
\definecolor{mine}{rgb}{0.1,0.2,0.3} \setlength{\tabcolsep}{4pt}
"""
    pre = r"\newcommand{\myconst}{12.5}" + "\n" + r"\usepackage[margin=2.5cm]{geometry}" + "\n"
    assert literals(project, body, pre) == []


def test_parsing_keeps_the_precision_a_literal_claims():
    assert parse_numbers(r"93.2\%") == [(93.2, 1)]
    assert parse_numbers("18{,}535") == [(18535.0, 0)]
    assert parse_numbers(r"1.2\times10^{-3}") == [(pytest.approx(0.0012), 4)]
    assert parse_numbers(r"91.2 \pm 0.4") == [(91.2, 1), (0.4, 1)]
    assert parse_numbers(r"2.5\times") == [(2.5, 1)]
    assert rounds_to(0.93249, 0.93, 2) and not rounds_to(0.936, 0.93, 2)
    assert rounds_to(2.25, 2.3, 1)                                   # half up, not to even


def test_masking_keeps_offsets():
    src = r"a \label{x9} b \vouch[.1f]{k} c"
    assert len(mask(src, False)) == len(src)


def test_matching_splits_cited_from_invented(proj):
    paper(proj, r"""
Accuracy is 93.2\% ($93.2 \pm 0.1$\%) on 18{,}535 images over 10 seeds;
it is 2.46\times faster with a learning rate of $1.2\times10^{-3}$.
We also report 97.1\% and 0.45 here, and 93.4\% which is close.
""")
    got = {i.subject: i for i in lint_issues(proj)}
    bare = {k for k, i in got.items() if i.check == "bare-number"}
    assert bare == {r"93.2\%", r"93.2 \pm 0.1", "18{,}535", "10", r"2.46\times",
                    r"1.2\times10^{-3}"}
    assert got[r"93.2\%"].fix == r"\vouch[.1pct]{cifar.resnet.acc.mean}"
    assert "it is cifar.resnet.acc.mean" in got[r"93.2\%"].message
    assert got["10"].fix == r"\vouch[d]{exp.param.seeds}"               # a param, not a .n
    assert got[r"93.2 \pm 0.1"].fix == r"\vouch{cifar.resnet.acc}"
    assert got["18{,}535"].fix == r"\vouch[d]{cifar.n_images}"
    assert {k for k, i in got.items() if i.check == "no-source"} == {r"97.1\%", "0.45", r"93.4\%"}
    assert "closest recorded value is cifar.resnet.acc.max = 93.3" in got[r"93.4\%"].message
    assert all(i.severity == "warning" for i in got.values())
    assert all(i.line for i in got.values())


def test_exemptions_and_levels(proj):
    proj.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n[lint]\nlevel = "error"\n'
                             'allow = [{ pattern = "GF\\\\(2\\\\)|bootstrap 95", why = "notation" }]\n')
    paper(proj, "We use a bootstrap 95\\% interval.\n"
                "Typed 0.77 here. % vouch: ignore\n"
                "And 0.66 there.\n")
    got = lint_issues(proj)
    assert [(i.subject, i.severity) for i in got] == [("0.66", "error")]
    proj.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n[lint]\nlevel = "off"\n')
    assert lint_issues(proj) == []


def test_strict_makes_lint_fail_the_check(proj):
    paper(proj, "Typed 0.77 here.")
    from vouch.build import build
    build(Config.load(proj.root))
    assert run_check(Config.load(proj.root), check_env=False).ok
    assert not run_check(Config.load(proj.root), strict=True, check_env=False).ok
