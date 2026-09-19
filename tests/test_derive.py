"""vouch_values.py: derived values, claims, tables and aliases (SPEC §5), and figures."""

import json
import math

import pytest
from conftest import edit

from vouch import cli
from vouch.build import build
from vouch.check import run_check
from vouch.config import Config
from vouch.values import Stat
from vouch.verdict import Verdict, all_of, any_of, approx, as_verdict, between, ge, gt, le, lt

# ---------------------------------------------------------------------------
# claim helpers (pure)
# ---------------------------------------------------------------------------


def test_comparisons_explain_themselves_and_measure_their_margin():
    v = gt(0.932, 0.912)
    assert v.holds and v.explanation == "0.932 > 0.912" and v.margin == pytest.approx(0.02 / 0.912)
    assert bool(v) and not lt(0.932, 0.912)
    assert lt(1, 2).margin == pytest.approx(0.5) and le(2, 2).holds and ge(2, 2).margin == 0
    fail = gt(1, 2)
    assert not fail.holds and fail.margin == pytest.approx(-0.5)
    assert gt(Stat.of([1.0, 3.0]), 1.5).explanation == "2 > 1.5"     # a Stat by its mean
    assert gt(1, 0).margin == 1.0                                     # boundary 0: relative to a
    assert not gt(float("nan"), 1).holds


def test_ranges_tolerances_and_combinations():
    assert between(5, 0, 10).holds and between(5, 0, 10).margin == pytest.approx(0.5)
    assert not between(11, 0, 10).holds
    a = approx(1.02, 1.0, rel=0.05)
    assert a.holds and a.margin == pytest.approx(0.03) and "within 2.0% of 1" in a.explanation
    assert not approx(1.2, 1.0).holds
    both = all_of(gt(3, 2), gt(3, 2.9), True)
    assert both.holds and both.margin == pytest.approx(0.1 / 2.9)
    assert " and " in both.explanation and "true" in both.explanation
    one = any_of(gt(1, 2), gt(3, 2))
    assert one.holds and one.margin == pytest.approx(0.5)
    assert as_verdict(True) == Verdict(True, "true", None) and as_verdict(3.0) is None
    with pytest.raises(TypeError, match="compares numbers"):
        gt("a", 1)


# ---------------------------------------------------------------------------
# a project with recorded results and a vouch_values.py
# ---------------------------------------------------------------------------

EXP = '''
    import vouch

    vouch.record_all({"resnet": {"acc": vouch.Stat.of([0.93, 0.94, 0.92])},
                      "vit": {"acc": vouch.Stat.of([0.90, 0.91, 0.92])}},
                     prefix="cifar", desc="accuracy")
'''


@pytest.fixture
def proj(project):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n')
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    return project


def paper(project, body: str) -> None:
    project.write("paper/main.tex", "\\documentclass{article}\n\\usepackage{vouch}\n"
                  "\\begin{document}\n" + body + "\n\\end{document}\n")


def derived(project) -> dict:
    return json.loads((project.root / ".vouch" / "derived.json").read_text(encoding="utf-8"))


def values_file(project) -> str:
    return (project.root / "paper" / "vouch-values.tex").read_text(encoding="utf-8")


def issues(project, strict=False) -> dict[str, list]:
    rep = run_check(Config.load(project.root), strict=strict, check_env=False)
    out: dict[str, list] = {}
    for i in rep.issues:
        out.setdefault(i.check, []).append(i)
    return out


VALUES = '''
    import vouch

    @vouch.derive("cifar.gap", fmt=".1f", unit="points", better="higher",
                  desc="ResNet minus ViT, points")
    def gap(v):
        return 100 * (v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"])

    @vouch.derive("cifar.both", desc="{-1} of both", returns=("lo", "hi"))
    def both(v):
        return min(v["cifar.resnet.acc.mean"], v["cifar.vit.acc.mean"]), v["cifar.gap"]

    @vouch.claim("cifar.resnet_wins", desc="ResNet beats ViT")
    def wins(v):
        return vouch.gt(v["cifar.resnet.acc"], v["cifar.vit.acc"])

    @vouch.table("summary", columns=["model", "acc"], row_key="model", highlight={"acc": "max"})
    def summary(v):
        return [[m, v[f"cifar.{m}.acc"]] for m in ("resnet", "vit")]
'''


def test_derived_values_claims_and_tables(proj, capsys):
    proj.write("vouch_values.py", VALUES)
    paper(proj, "Gap \\vouch{cifar.gap}, low \\vouch{cifar.both.lo}. "
                "\\vouchclaim{cifar.resnet_wins}{ResNet wins.}\n"
                "\\begin{tabular}{lr}\\vouchtable{summary}\\end{tabular}")
    assert cli.main(["build", "--root", str(proj.root)]) == 0
    out = capsys.readouterr().out
    assert "evaluated vouch_values.py (4 definitions) -> .vouch/derived.json" in out
    d = derived(proj)["definitions"]
    gap = d["cifar.gap"]
    assert gap["function"] == "vouch_values.py::gap" and gap["site"] == "vouch_values.py:3"
    assert set(gap["deps"]) == {"cifar.resnet.acc.mean", "cifar.vit.acc.mean"}
    assert gap["runs"] == ["exp"]
    assert gap["values"]["cifar.gap"]["value"] == pytest.approx(2.0)
    assert gap["values"]["cifar.gap"]["unit"] == "points"
    both = d["cifar.both"]["values"]
    assert both["cifar.both.hi"]["desc"] == "hi of both" and "cifar.gap" in d["cifar.both"]["deps"]
    claim = d["cifar.resnet_wins"]["claim"]
    assert claim["holds"] and claim["explanation"] == "0.93 > 0.91"
    assert claim["margin"] == pytest.approx(0.02 / 0.91, abs=1e-6)
    v = values_file(proj)
    assert r"\vouch@set{cifar.gap}{}{2.0}" in v
    assert r"\vouch@claim{cifar.resnet_wins}{1}" in v
    assert r"derived by \texttt{vouch\_values.py::gap} at \texttt{vouch\_values.py:3}" in v
    body = (proj.root / "paper" / "vouch-tables" / "summary.tex").read_text(encoding="utf-8")
    assert "from vouch_values.py::summary" in body and r"\vouchbest{\vouch{summary.resnet.acc}}" in body
    assert issues(proj).get("out-of-sync") is None
    assert cli.main(["trace", "--root", str(proj.root), "cifar.gap"]) == 0
    tr = capsys.readouterr().out
    assert "derived   vouch_values.py::gap   at vouch_values.py:3" in tr
    assert "from      cifar.resnet.acc.mean = 0.93" in tr and "runs      exp (fresh)" in tr
    assert "feeds     cifar.both (derived)" in tr
    assert cli.main(["trace", "--root", str(proj.root), "cifar.resnet.acc"]) == 0
    assert "feeds     cifar.both (derived) · cifar.gap (derived) · cifar.resnet_wins (claim) · " \
           "summary (table)" in capsys.readouterr().out


def test_an_unchanged_project_builds_without_running_the_definitions(proj):
    proj.write("vouch_values.py", '''
        import pathlib
        import vouch

        @vouch.derive("cifar.gap", desc="gap")
        def gap(v):
            with open(pathlib.Path(__file__).parent / "evaluations.txt", "a") as fh:
                fh.write("x")
            return v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"]
    ''')
    paper(proj, "\\vouch{cifar.gap}")
    count = proj.root / "evaluations.txt"
    build(Config.load(proj.root))
    res = build(Config.load(proj.root))
    assert count.read_text() == "x" and not res.written
    edit(proj.root / "vouch_values.py", "import pathlib", "import pathlib  # a comment")
    build(Config.load(proj.root))
    assert count.read_text() == "x"                           # comments don't count
    edit(proj.root / "vouch_values.py", "- v[", "+ v[")
    build(Config.load(proj.root))
    assert count.read_text() == "xx"


def test_check_says_build_when_anything_moved(proj):
    proj.write("vouch_values.py", VALUES)
    paper(proj, "\\vouch{cifar.gap} \\vouchclaim{cifar.resnet_wins}{wins}")
    got = issues(proj)                            # never built: one error, not unknown keys
    assert [i.message for i in got["out-of-sync"]] == [
        "vouch_values.py has not been evaluated (run `vouch build`)",
        "paper/vouch-values.tex has not been built"]
    assert "unknown-key" not in got
    build(Config.load(proj.root))
    assert "out-of-sync" not in issues(proj)
    # the code changed
    edit(proj.root / "vouch_values.py", "100 * (", "99 * (")
    msg = issues(proj)["out-of-sync"][0].message
    assert "derived values are out of date: vouch_values.py changed" in msg
    build(Config.load(proj.root))
    # a value it read changed
    edit(proj.root / "exp.py", "0.90, 0.91", "0.80, 0.81")
    proj.run("exp.py", check=True)
    msg = issues(proj)["out-of-sync"][0].message
    assert "cifar.vit.acc.mean changed (read by cifar.gap)" in msg
    build(Config.load(proj.root))
    assert "out-of-sync" not in issues(proj)
    # a new definition, cited before it is built, is not an unknown key
    proj.write("vouch_values.py", VALUES + '''
    @vouch.derive("cifar.new", desc="new")
    def new(v):
        return 1.0
    ''')
    paper(proj, "\\vouch{cifar.gap} \\vouch{cifar.new}")
    got = issues(proj)
    assert "unknown-key" not in got and got["out-of-sync"]


def test_hand_edits_and_removal(proj):
    proj.write("vouch_values.py", VALUES)
    paper(proj, "\\vouch{cifar.gap}")
    build(Config.load(proj.root))
    path = proj.root / ".vouch" / "derived.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"value": 2.0', '"value": 5.0'),
                    encoding="utf-8")
    assert issues(proj)["store-edited"]
    build(Config.load(proj.root))                      # rebuilt from the definitions
    assert "store-edited" not in issues(proj)
    (proj.root / "vouch_values.py").unlink()
    paper(proj, "no values")
    res = build(Config.load(proj.root))
    assert not path.exists() and res.ctx.derived.removed == path


def test_a_stale_run_makes_what_is_derived_from_it_stale(proj):
    proj.write("vouch_values.py", VALUES)
    paper(proj, "\\vouch{cifar.gap}")
    build(Config.load(proj.root))
    edit(proj.root / "exp.py", "prefix=\"cifar\"", "prefix=\"cifar\", fmt=None")
    stale = issues(proj)["stale"][0]
    assert stale.subject == "run:exp" and "the paper cites cifar.gap" in stale.message


def test_aliases(proj, capsys):
    proj.write("vouch_values.py", '''
        import vouch

        vouch.alias("r", "cifar.resnet")
        vouch.alias("rr", "r.acc")                   # an alias of an alias
        vouch.alias("nothing", "cifar.nope")

        @vouch.derive("r_gap", desc="through the alias")
        def gap(v):
            return v["r.acc.mean"] - v["cifar.vit.acc.mean"]
    ''')
    paper(proj, "\\vouch{r.acc} \\vouch{r.acc.mean} \\vouch{rr.std} \\vouch{r_gap}")
    res = build(Config.load(proj.root))
    v = values_file(proj)
    assert r"\vouch@set{r.acc}{}" in v and r"\vouch@set{rr.std}{}" in v
    assert r"alias of \texttt{cifar.resnet.acc}" in v
    assert derived(proj)["definitions"]["r"] == {"kind": "alias", "site": "vouch_values.py:3",
                                                   "target": "cifar.resnet"}
    assert [i.message for i in res.ctx.project_issues if i.check == "alias-target"] == [
        "alias nothing -> cifar.nope: nothing is recorded under cifar.nope (vouch_values.py:5)"]
    got = issues(proj)
    assert "unknown-key" not in got and "out-of-sync" not in got
    assert cli.main(["trace", "--root", str(proj.root), "r.acc"]) == 0
    out = capsys.readouterr().out
    assert "alias of  cifar.resnet.acc" in out and "in run exp" in out


def test_inputs_are_loaded_and_must_come_from_a_run(project):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n'
                                '[inputs]\nexternal = ["data/raw"]\n')
    project.write("exp.py", '''
        import vouch

        with open("sweep.csv", "w") as fh:
            fh.write("lr,val_acc\\n0.1,0.8\\n0.01,0.9\\n")
        vouch.artifact("sweep.csv")
        vouch.record("done", 1, desc="d")
    ''')
    project.run("exp.py", check=True)
    project.write("data/raw/labels.json", '{"n": 3}')
    project.write("loose.csv", "a\n1\n")
    project.write("vouch_values.py", '''
        import vouch

        @vouch.derive("sweep.best_lr", inputs=["sweep.csv"], fmt=".0e", desc="best lr")
        def best(v, sweep):
            return float(max(sweep, key=lambda r: float(r["val_acc"]))["lr"])

        @vouch.derive("labels.n", inputs="data/raw/labels.json", desc="labels")
        def n(v, labels):
            return labels["n"]

        @vouch.derive("loose.a", inputs=["loose.csv"], desc="from nowhere")
        def loose(v, rows):
            return int(rows[0]["a"])
    ''')
    paper(project, "\\vouch{sweep.best_lr} \\vouch{labels.n} \\vouch{loose.a}")
    build(Config.load(project.root))
    d = derived(project)["definitions"]
    assert d["sweep.best_lr"]["values"]["sweep.best_lr"]["value"] == 0.01
    assert d["sweep.best_lr"]["runs"] == ["exp"] and "sweep.csv" in d["sweep.best_lr"]["inputs"]
    assert d["labels.n"]["values"]["labels.n"]["value"] == 3
    got = issues(project)
    assert [i.subject for i in got["untracked-input"]] == ["loose.a"]
    assert got["untracked-input"][0].severity == "error"
    edit(project.root / "data/raw/labels.json", "3", "4")
    assert "input data/raw/labels.json changed (read by labels.n)" in \
        issues(project)["out-of-sync"][0].message


def test_definition_errors_are_reported_where_they_happen(proj):
    proj.write("vouch_values.py", '''
        import vouch

        @vouch.derive("bad.div", desc="d")
        def div(v):
            return v["cifar.gap"] / 0

        @vouch.derive("cifar.gap", desc="d")
        def gap(v):
            return 1.0

        @vouch.derive("bad.typo", desc="d")
        def typo(v):
            return v["cifar.resnet.ac"]

        @vouch.derive("bad.after", desc="d")
        def after(v):
            return v["bad.div"] + 1

        @vouch.derive("loop.a", desc="d")
        def a(v):
            return v["loop.b"]

        @vouch.derive("loop.b", desc="d")
        def b(v):
            return v["loop.a"]

        @vouch.claim("bad.claim", desc="d")
        def c(v):
            return 3

        @vouch.derive("cifar.gap", desc="again")
        def gap2(v):
            return 2.0

        @vouch.derive("bad key!", desc="d")
        def k(v):
            return 1

        @vouch.derive("bad.records", desc="d")
        def rec(v):
            vouch.record("x", 1)
            return 1
    ''')
    paper(proj, "\\vouch{cifar.gap}")
    build(Config.load(proj.root))
    got = issues(proj)
    msgs = {i.subject: (i.message, i.file, i.line) for i in got["derive-error"]}
    assert msgs["bad.div"][0] == "bad.div: ZeroDivisionError: float division by zero " \
                                 "(at vouch_values.py:5)"
    assert msgs["bad.div"][1:] == ("vouch_values.py", 3)
    assert "'cifar.resnet.ac' is not recorded by any run (did you mean cifar.resnet.acc" \
        in msgs["bad.typo"][0]
    assert msgs["bad.after"][0] == "bad.after reads bad.div, which could not be computed"
    assert "a claim returns a bool or a Verdict" in msgs["bad.claim"][0]
    assert msgs["cifar.gap"][0] == "cifar.gap is defined twice (vouch_values.py:7 and " \
                                   "vouch_values.py:31)"
    assert "'bad key!' is not a valid key" in msgs["bad key!"][0]
    assert "must not record values" in msgs["bad.records"][0]
    cycles = sorted(i.subject for i in got["derive-cycle"])
    assert cycles == ["loop.a", "loop.b"]
    assert "loop.a -> loop.b -> loop.a" in got["derive-cycle"][0].message
    # the first definition still counts, and a build reports the same problems
    assert r"\vouch@set{cifar.gap}{}{1}" in values_file(proj)


def test_an_import_error_is_reported(proj):
    proj.write("vouch_values.py", "import vouch\n\nraise RuntimeError('boom')\n")
    paper(proj, "x")
    build(Config.load(proj.root))
    err = issues(proj)["derive-error"][0]
    assert err.message == "vouch_values.py failed to import: RuntimeError: boom"
    assert (err.file, err.line) == ("vouch_values.py", 3)


def test_listing_keys_is_a_dependency(proj):
    proj.write("vouch_values.py", '''
        import vouch

        @vouch.derive("cifar.best", desc="best model")
        def best(v):
            return max(v.keys("cifar.*.acc"), key=lambda k: v[k].mean).split(".")[1]
    ''')
    paper(proj, "\\vouch{cifar.best}")
    build(Config.load(proj.root))
    assert r"\vouch@set{cifar.best}{}{resnet}" in values_file(proj)
    edit(proj.root / "exp.py", '"vit": {', '"deit": {"acc": vouch.Stat.of([0.99, 0.98])}, "vit": {')
    proj.run("exp.py", check=True)
    assert "keys:cifar.*.acc changed" in issues(proj)["out-of-sync"][0].message
    build(Config.load(proj.root))
    assert r"\vouch@set{cifar.best}{}{deit}" in values_file(proj)


def test_derived_modules_import_their_helpers_afresh(proj):
    proj.write("helpers.py", "SCALE = 100\n")
    proj.write("vouch_values.py", '''
        import vouch
        from helpers import SCALE

        @vouch.derive("cifar.gap", desc="gap")
        def gap(v):
            return SCALE * (v["cifar.resnet.acc.mean"] - v["cifar.vit.acc.mean"])
    ''')
    paper(proj, "\\vouch{cifar.gap}")
    build(Config.load(proj.root))
    assert set(derived(proj)["code"]) == {"helpers.py", "vouch_values.py"}
    edit(proj.root / "helpers.py", "100", "1000")
    assert "helpers.py changed" in issues(proj)["out-of-sync"][0].message
    build(Config.load(proj.root))
    assert r"\vouch@set{cifar.gap}{}{20}" in values_file(proj)


# ---------------------------------------------------------------------------
# claims: fragile, and verdicts recorded in runs
# ---------------------------------------------------------------------------

def test_fragile_claims(project):
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n'
                                '[changes]\nclaim_margin = 0.05\n')
    project.write("exp.py", '''
        import vouch

        vouch.claim("thin", vouch.gt(0.93, 0.92), desc="thin win")
        vouch.claim("wide", vouch.gt(0.93, 0.5), desc="wide win")
        vouch.claim("plain", True, desc="a bool")
    ''')
    project.run("exp.py", check=True)
    paper(project, "\\vouchclaim{thin}{a} \\vouchclaim{wide}{b} \\vouchclaim{plain}{c}")
    rec = project.record("exp")["claims"]
    assert rec["thin"]["explanation"] == "0.93 > 0.92" and rec["thin"]["margin"] == \
        pytest.approx(0.01 / 0.92, abs=1e-6)
    build(Config.load(project.root))
    got = issues(project)
    assert [i.subject for i in got["fragile"]] == ["thin"]
    assert "holds by only 1.09% (0.93 > 0.92)" in got["fragile"][0].message
    assert r"0.93 \ensuremath{>} 0.92 (margin 1.1\%)" in values_file(project)


def test_table_overrides_in_vouch_toml(proj):
    proj.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n'
                             '[tables.summary]\nhighlight = { acc = "min" }\nmidrules = [1]\n')
    proj.write("vouch_values.py", VALUES)
    paper(proj, "\\begin{tabular}{lr}\\vouchtable{summary}\\end{tabular}")
    build(Config.load(proj.root))
    body = (proj.root / "paper" / "vouch-tables" / "summary.tex").read_text(encoding="utf-8")
    assert r"\vouchbest{\vouch{summary.vit.acc}}" in body and r"\midrule" in body


def test_values_modules_are_inert_outside_build(project):
    project.write("vouch_values.py", '''
        import vouch

        @vouch.derive("k", desc="d")
        def k(v):
            raise AssertionError("never called outside vouch build")

        vouch.alias("a", "b")
    ''')
    project.write("use.py", "import vouch_values\nprint(vouch_values.k.__name__)\n")
    assert project.run("use.py", check=True).stdout.strip() == "k"


# ---------------------------------------------------------------------------
# figures saved with matplotlib
# ---------------------------------------------------------------------------

FAKE_MPL = {
    "matplotlib/__init__.py": 'rcParams = {"savefig.format": "png"}\n',
    "matplotlib/figure.py": '''
        import os


        class Figure:
            def savefig(self, fname, *, format=None, **kw):
                if isinstance(fname, (str, os.PathLike)):
                    path = os.fspath(fname)
                    if not os.path.splitext(path)[1]:
                        path += "." + (format or "png")
                    with open(path, "w") as fh:
                        fh.write(f"figure {kw}")
                else:
                    fname.write(b"x")
    ''',
    "matplotlib/pyplot.py": '''
        from .figure import Figure
        _fig = Figure()


        def savefig(*args, **kwargs):
            return _fig.savefig(*args, **kwargs)
    ''',
}


@pytest.mark.parametrize("first", ["vouch", "matplotlib"])
def test_saved_figures_become_artifacts(project, first):
    for rel, text in FAKE_MPL.items():
        project.write(rel, text)
    order = ["import vouch", "import matplotlib.pyplot as plt"]
    if first == "matplotlib":
        order.reverse()
    project.write("exp.py", "import io\n" + "\n".join(order) + '''
import os
os.makedirs("paper/figs", exist_ok=True)
plt.savefig("paper/figs/curve.pdf", dpi=100)
plt.savefig("paper/figs/bare", format="svg")
plt.savefig(io.BytesIO())
''')
    project.run("exp.py", check=True)
    arts = project.record("exp")["artifacts"]
    assert set(arts) == {"paper/figs/curve.pdf", "paper/figs/bare.svg"}
    assert arts["paper/figs/curve.pdf"]["kind"] == "figure"
    assert arts["paper/figs/curve.pdf"]["site"] == "exp.py:6"


def test_a_saved_figure_is_tracked_in_the_paper(project):
    for rel, text in FAKE_MPL.items():
        project.write(rel, text)
    project.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n')
    project.write("exp.py", '''
        import os
        import vouch
        import matplotlib.pyplot as plt

        os.makedirs("paper/figs", exist_ok=True)
        plt.savefig("paper/figs/curve.pdf")
    ''')
    project.run("exp.py", check=True)
    paper(project, "\\includegraphics{figs/curve}")
    build(Config.load(project.root))
    got = issues(project)
    assert "figure-untracked" not in got and "figure-missing" not in got
    edit(project.root / "exp.py", "curve.pdf\")", "curve.pdf\", dpi=1)")
    assert got.get("figure-stale") is None
    assert issues(project)["figure-stale"][0].subject == "paper/figs/curve.pdf"
    assert math.isfinite(1.0)
