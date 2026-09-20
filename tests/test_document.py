"""vouch document (SPEC §12.3): a script's docstrings next to its real values."""

import json

import pytest

from vouch import cli
from vouch.build import plan
from vouch.config import Config
from vouch.document import render_markdown, render_text, summarize, to_json

EXP = '''
    """Learning curves: a linear model against k-nearest neighbours."""

    import vouch

    @vouch.track(over="seed")
    def evaluate(dataset, model, seed=0):
        """Train one model; return its held-out accuracy."""
        base = {"resnet": 0.93, "vit": 0.91}[model]
        return {"acc": base + seed / 1000}

    def main():
        for m in ("resnet", "vit"):
            for s in range(3):
                evaluate("cifar", m, seed=s)
        vouch.record("cifar.n_test", 1000, desc="test images")

    main()
'''


@pytest.fixture
def proj(project, monkeypatch):
    # no [[paper]] at all: `vouch document` must not need one
    project.write("vouch.toml", "")
    project.write("exp.py", EXP)
    project.run("exp.py", check=True)
    monkeypatch.chdir(project.root)
    return project


def test_summarizes_one_script_with_no_paper_configured(proj):
    ctx = plan(Config.load(proj.root), need_paper=False, check_env=False)
    summaries = summarize(ctx, "exp.py")
    assert len(summaries) == 1
    s = summaries[0]
    assert s.script == "exp.py"
    assert s.context == "Learning curves: a linear model against k-nearest neighbours."
    assert s.state == "fresh"
    names = {f.qualname for f in s.functions}
    assert names == {"evaluate", "<module>"}
    ev = next(f for f in s.functions if f.qualname == "evaluate")
    assert ev.context == "Train one model; return its held-out accuracy."
    keys = {k.key for k in ev.keys}
    assert "evaluate.cifar.resnet.acc" in keys and "evaluate.cifar.vit.acc" in keys
    top = next(f for f in s.functions if f.qualname == "<module>")
    assert [k.key for k in top.keys] == ["cifar.n_test"]


def test_project_wide_mode_with_no_argument(proj):
    ctx = plan(Config.load(proj.root), need_paper=False, check_env=False)
    assert [s.script for s in summarize(ctx, None)] == ["exp.py"]


def test_unknown_script_returns_nothing(proj):
    ctx = plan(Config.load(proj.root), need_paper=False, check_env=False)
    assert summarize(ctx, "nope.py") == []


def test_text_and_markdown_rendering_include_real_values(proj):
    ctx = plan(Config.load(proj.root), need_paper=False, check_env=False)
    summaries = summarize(ctx, "exp.py")
    text = "\n".join(render_text(summaries))
    assert "Train one model; return its held-out accuracy." in text
    assert "evaluate.cifar.resnet.acc" in text
    md = render_markdown(summaries)
    assert "## `exp.py`" in md and "### `evaluate`" in md
    assert "| `evaluate.cifar.resnet.acc` |" in md
    assert to_json(summaries)[0]["script"] == "exp.py"


def test_cli_never_edits_the_script(proj):
    before = (proj.root / "exp.py").read_text(encoding="utf-8")
    assert cli.main(["document", "exp.py"]) == 0
    assert (proj.root / "exp.py").read_text(encoding="utf-8") == before


def test_cli_md_snapshot(proj, tmp_path):
    out = tmp_path / "doc.md"
    assert cli.main(["document", "exp.py", "--md", str(out)]) == 0
    assert "evaluate.cifar.resnet.acc" in out.read_text(encoding="utf-8")


def test_cli_json(proj, capsys):
    assert cli.main(["document", "exp.py", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["ok"] is True
    assert got["scripts"][0]["script"] == "exp.py"


def test_cli_reports_no_run_instead_of_guessing(proj, capsys):
    assert cli.main(["document", "nope.py"]) == 1
    assert "no recorded run for nope.py" in capsys.readouterr().err
