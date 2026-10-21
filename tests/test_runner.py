"""vouch run (any command) and vouch import (existing results files), SPEC §14."""

import json
import sys

import pytest
from conftest import edit

from vouch import cli
from vouch.check import run_check
from vouch.config import Config
from vouch.freshness import assess
from vouch.store import load_runs

GEN = '''
import json, os
out = {"values": {"julia.acc": {"value": 0.912, "fmt": ".1pct", "desc": "ViT top-1",
                                "better": "higher"},
                  "julia.loss": {"value": {"mean": 0.3, "std": 0.01, "n": 5}, "desc": "loss"}},
       "claims": {"julia.ok": {"holds": True, "desc": "it converged"}},
       "artifacts": ["results/plot.txt"]}
os.makedirs("results", exist_ok=True)
open("results/plot.txt", "w").write("plot")
json.dump(out, open(os.environ["VOUCH_VALUES"], "w"))
'''


@pytest.fixture
def proj(project, monkeypatch):
    project.write("src/helpers.py", "def f():\n    return 1\n")
    project.write("src/config.yaml", "lr: 0.1\n")
    project.write("gen.py", GEN)
    monkeypatch.chdir(project.root)
    return project


def states(project):
    cfg = Config.load(project.root)
    return assess(cfg, load_runs(cfg.store), check_env=False)


def test_any_command_hands_values_back(proj, capsys):
    rc = cli.main(["run", "jl", "--dep", "src", "--", sys.executable, "-c",
                   "exec(open('gen.py').read())"])
    assert rc == 0
    assert "recorded run jl: 2 value(s), 1 artifact(s)" in capsys.readouterr().out
    rec = proj.record("jl")
    assert rec["command"][:5] == ["vouch", "run", "jl", "--dep", "src"]
    assert rec["code"]["granularity"] == "deps"
    assert set(rec["code"]["units"]) == {"src/helpers.py::<module>", "src/helpers.py::f"}
    assert "src/config.yaml" in rec["inputs"]
    assert rec["values"]["julia.acc"]["fmt"] == ".1pct" and rec["values"]["julia.acc"]["site"] == \
        "$VOUCH_VALUES"
    assert rec["values"]["julia.loss"]["type"] == "stat"
    assert rec["claims"]["julia.ok"]["holds"] is True
    assert "results/plot.txt" in rec["artifacts"]
    assert states(proj)["jl"].state == "fresh"
    edit(proj.root / "src/helpers.py", "return 1", "return 2")      # declared code changed
    assert states(proj)["jl"].state == "stale"


def test_a_non_python_dependency_is_hashed_by_content(proj):
    assert cli.main(["run", "jl", "--dep", "src/config.yaml", "--", sys.executable, "-c",
                     "exec(open('gen.py').read())"]) == 0
    edit(proj.root / "src/config.yaml", "0.1", "0.2")
    st = states(proj)["jl"]
    assert st.state == "stale" and st.reasons[0].subject == "src/config.yaml"


def test_shorthand_values_and_results_files(proj):
    code = "import json, os; json.dump({'a': {'b': 0.5}}, open(os.environ['VOUCH_VALUES'], 'w'))"
    assert cli.main(["run", "short", "--prefix", "p", "--", sys.executable, "-c", code]) == 0
    assert list(proj.record("short")["values"]) == ["p.a.b"]
    proj.write("results/m.csv", "model,acc\nresnet,0.93\nvit,0.91\n")
    assert cli.main(["run", "csv", "--values", "results/m.csv", "--row-key", "model", "--",
                     sys.executable, "-c", "pass"]) == 0
    rec = proj.record("csv")
    assert set(rec["values"]) == {"resnet.acc", "vit.acc"} and "results/m.csv" in rec["inputs"]


needs_monitoring = pytest.mark.skipif(sys.version_info < (3, 12),
                                      reason="function-level tracking needs sys.monitoring")


@needs_monitoring
def test_python_commands_are_tracked_by_function(proj):
    proj.write("train.py", '''
        import vouch

        @vouch.track(desc="score")
        def score(model):
            return 0.5

        score("x")
    ''')
    assert cli.main(["run", "py", "--dep", "src", "--out", "gen.py", "--",
                     "python", "train.py"]) == 0
    rec = proj.record("py")
    assert rec["code"]["granularity"] == "function" and rec["entry"] == "train.py"
    assert {"train.py::score", "src/helpers.py::f"} <= set(rec["code"]["units"])
    assert "score.x" in rec["values"] and "gen.py" in rec["artifacts"]
    assert rec["command"] == ["vouch", "run", "py", "--dep", "src", "--out", "gen.py", "--",
                              "python", "train.py"]


def test_a_failing_command_records_nothing(proj, capsys):
    assert cli.main(["run", "jl", "--", sys.executable, "-c", "exec(open('gen.py').read())"]) == 0
    before = (proj.root / ".vouch/runs/jl.json").read_bytes()
    assert cli.main(["run", "jl", "--", sys.executable, "-c", "import sys; sys.exit(3)"]) == 3
    assert "exited 3; run jl not recorded" in capsys.readouterr().out
    assert (proj.root / ".vouch/runs/jl.json").read_bytes() == before
    proj.write("dies.py", "import sys, vouch\nvouch.record('x', 1, desc='d')\nsys.exit(4)\n")
    assert cli.main(["run", "jl", "--", "python", "dies.py"]) == 4
    assert (proj.root / ".vouch/runs/jl.json").read_bytes() == before     # the child's is undone
    assert cli.main(["run", "jl"]) == 2                                  # no command



def test_import_registers_a_file_with_declared_provenance(proj, capsys):
    proj.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n'
                             '[metrics]\n"*.acc" = { desc = "accuracy", fmt = ".1pct" }\n')
    proj.write("results/eval.json", json.dumps({"resnet": {"acc": 0.93}, "vit": {"acc": 0.91}}))
    assert cli.main(["import", "results/eval.json", "--run", "ev", "--prefix", "imagenet",
                     "--producer", "src/helpers.py", "--command", "python eval.py --split val"]) == 0
    out = capsys.readouterr().out
    assert "imported 2 value(s) into run ev (prefix imagenet) · granularity: declared (1 file)" in out
    assert "described: 2/2" in out
    rec = proj.record("ev")
    assert rec["imported"] == {"file": "results/eval.json", "command": "declared",
                               "producers": ["src/helpers.py"]}
    assert rec["command"] == ["python", "eval.py", "--split", "val"]
    assert rec["code"]["granularity"] == "declared" and "results/eval.json" in rec["inputs"]
    proj.write("paper/main.tex", "\\documentclass{article}\\usepackage{vouch}\\begin{document}\n"
               "\\vouch{imagenet.resnet.acc}\n\\end{document}\n")
    assert cli.main(["build"]) == 0
    rep = run_check(Config.load(proj.root), check_env=False)
    imp = [i for i in rep.issues if i.check == "imported"]
    assert imp and imp[0].severity == "info"
    capsys.readouterr()
    assert cli.main(["trace", "imagenet.resnet.acc"]) == 0
    tr = capsys.readouterr().out
    assert "imported  from results/eval.json into run ev" in tr and "(declared, not observed)" in tr
    assert cli.main(["export"]) == 0
    assert "ev (imported)" in capsys.readouterr().out
    # a hand edit of the results file, or of the declared producer, makes it stale
    edit(proj.root / "results/eval.json", "0.93", "0.95")
    assert states(proj)["ev"].state == "stale"


def test_a_relative_executable_path_is_started(proj, tmp_path):
    """`vouch run ID -- .venv/Scripts/python.exe x.py` failed on Windows with WinError 2:
    process creation does not resolve a relative path with forward slashes. The path is
    now resolved against the working directory, and recorded as it was typed."""
    import os
    import shutil
    from pathlib import Path
    venv = proj.root / "tools" / "bin"
    venv.mkdir(parents=True)
    exe = venv / Path(sys.executable).name
    shutil.copy2(sys.executable, exe)
    for dll in Path(sys.executable).parent.glob("*.dll"):    # a Windows venv's python needs them
        shutil.copy2(dll, venv / dll.name)
    cfg = Path(sys.executable).parent / "pyvenv.cfg"
    for c in (cfg, cfg.parent.parent / "pyvenv.cfg"):
        if c.is_file():
            shutil.copy2(c, venv.parent / "pyvenv.cfg")
    typed = Path(os.path.relpath(exe, proj.root)).as_posix()
    assert "/" in typed and not Path(typed).is_absolute()
    code = "import json, os; json.dump({'a': 1}, open(os.environ['VOUCH_VALUES'], 'w'))"
    assert cli.main(["run", "rel", "--", typed, "-c", code]) == 0
    assert proj.record("rel")["command"][-3] == typed


@pytest.mark.parametrize("cmd, at", [
    (["python", "train.py"], 0),
    (["python", "-c", "pass"], None),
    (["uv", "run", "python", "train.py"], 2),
    (["uv", "run", "--no-sync", "python", "exp/train.py", "--force"], 3),
    (["uv", "run", "--python", "python3", "python", "x.py"], 4),
    (["uv.exe", "run", "--group", "dev", "python3", "x.py"], 4),
    (["uv", "run", "pytest", "tests/test_x.py"], None),
    (["uv", "sync"], None),
    (["make", "all"], None),
])
def test_where_the_interpreter_is(cmd, at):
    from vouch.runner import _python_at
    assert _python_at(cmd) == at


@needs_monitoring
def test_uv_run_python_is_tracked_by_function(proj):
    """`uv run ... python script.py` runs a Python script, so it gets what `python
    script.py` gets: function-level tracking and the real interpreter in the record,
    instead of declared dependencies and an unknown Python."""
    import platform
    import shutil
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    proj.write("train.py", "def score():\n    return 0.5\n\nscore()\n")
    assert cli.main(["run", "uvpy", "--", "uv", "run", "--no-project", "--python",
                     sys.executable, "python", "train.py"]) == 0
    rec = proj.record("uvpy")
    assert rec["entry"] == "train.py" and rec["code"]["granularity"] == "function"
    assert "train.py::score" in rec["code"]["units"]
    assert rec["env"]["python"] == platform.python_version()
    assert rec["command"][-2:] == ["python", "train.py"]


def test_a_data_artifact_can_be_traced(proj, capsys):
    """`vouch trace FILE` answered only for figures, scripts and keys, so the provenance
    of a results table, a model or a built wheel -- an artifact of `vouch run --out`
    -- could not be looked up at all ("not a key, a tex file:line, a figure or a file
    any run used")."""
    assert cli.main(["run", "jl", "--dep", "src", "--", sys.executable, "-c",
                     "exec(open('gen.py').read())"]) == 0
    code = "open('results/sum.txt', 'w').write(open('results/plot.txt').read())"
    assert cli.main(["run", "reader", "--input", "results/plot.txt", "--out", "results/sum.txt",
                     "--", sys.executable, "-c", code]) == 0
    capsys.readouterr()
    assert cli.main(["trace", "results/plot.txt"]) == 0
    out = capsys.readouterr().out
    assert "written   by run jl (fresh)" in out and "as the run wrote it" in out
    assert "read by   run reader (fresh)" in out
    assert cli.main(["trace", "results/plot.txt", "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["type"] == "data" and got["written_by"][0]["run"] == "jl"
    (proj.root / "results/plot.txt").write_text("edited by hand", encoding="utf-8")
    assert cli.main(["trace", "results/plot.txt"]) == 0
    assert "differs from what the run wrote" in capsys.readouterr().out
    assert cli.main(["trace", "results/nothing.txt"]) == 1
    assert "a file a run wrote, read" in capsys.readouterr().err


def test_import_without_a_producer_is_a_warning(proj):
    proj.write("vouch.toml", '[[paper]]\nmain = "paper/main.tex"\n')
    proj.write("results/eval.json", json.dumps({"acc": 0.93}))
    assert cli.main(["import", "results/eval.json", "--run", "ev"]) == 0
    proj.write("paper/main.tex", "\\documentclass{article}\\usepackage{vouch}\\begin{document}\n"
               "\\vouch{acc}\n\\end{document}\n")
    cli.main(["build"])
    rep = run_check(Config.load(proj.root), check_env=False)
    imp = [i for i in rep.issues if i.check == "imported"]
    assert imp[0].severity == "warning" and "no --producer" in imp[0].message
    assert cli.main(["import", "missing.json", "--run", "x"]) == 2
